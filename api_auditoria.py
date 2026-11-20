import base64
import binascii
import ipaddress
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, List

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool


PROJECT_PATH = Path(__file__).resolve().parent
MODELS_PATH = PROJECT_PATH / "models"
MODEL_PATH = MODELS_PATH / "best.pt"
MODEL_FALLBACK_PATH = MODELS_PATH / "best_v3.pt"
ENGINE_PATH = MODELS_PATH / "best.engine"
FRONTEND_PATH = Path(__file__).resolve().parent / "frontend"
CERT_PATH = Path(__file__).resolve().parent / "cert.pem"
KEY_PATH = Path(__file__).resolve().parent / "key.pem"
PREDICT_CONFIDENCE = 0.20
PREDICT_IOU = 0.45
PREDICT_IMAGE_SIZE = 640
OCCUPANCY_THRESHOLD = 80
UMBRAL_POR_CLASE = {
    0: 0.35,
    1: 0.20,
    2: 0.30,
    3: 0.30,
}
NO_EMPTY_WARNING = (
    "No se detectaron espacios vacíos; la ocupación no pudo estimarse con certeza. "
    "Apunta directamente al anaquel real, llena el encuadre y enfoca."
)
CLASS_INFO = {
    0: ("OK", (0, 220, 0)),
    1: ("VACIO", (0, 0, 255)),
    2: ("DANADO", (0, 140, 255)),
    3: ("CAIDO", (0, 230, 255)),
}
model: Any = None
logger = logging.getLogger(__name__)


def calculate_metrics(
    detections: list[tuple[int, float, float, float, float]],
) -> dict[str, int | float | str | None]:
    botellas_ok = sum(int(class_id == 0) for class_id, *_ in detections)
    espacios = [item for item in detections if item[0] == 1]
    espacios_vacios = len(espacios)
    productos_danados = sum(int(class_id == 2) for class_id, *_ in detections)
    productos_caidos = sum(int(class_id == 3) for class_id, *_ in detections)

    bottle_widths = [x2 - x1 for class_id, x1, _, x2, _ in detections if class_id == 0]
    median_bottle_width = median(bottle_widths) if bottle_widths else None
    if median_bottle_width:
        faltantes_estimados = sum(
            max(1, round((x2 - x1) / median_bottle_width))
            for _, x1, _, x2, _ in espacios
        )
    else:
        faltantes_estimados = espacios_vacios

    if espacios_vacios == 0:
        porcentaje_ocupacion = None
        ocupacion_display = "N/D — REVISAR"
        estado = "REVISAR"
        advertencia = NO_EMPTY_WARNING
    else:
        total_observado = botellas_ok + espacios_vacios
        porcentaje_ocupacion = round((botellas_ok / total_observado) * 100, 1)
        ocupacion_display = f"{porcentaje_ocupacion}%"
        advertencia = None
        if (
            porcentaje_ocupacion < OCCUPANCY_THRESHOLD
            or productos_danados > 0
            or productos_caidos > 0
        ):
            estado = "RESURTIR"
        else:
            estado = "OPTIMO"

    return {
        "botellas_ok": botellas_ok,
        "espacios_vacios": espacios_vacios,
        "faltantes_estimados": faltantes_estimados,
        "porcentaje_ocupacion": porcentaje_ocupacion,
        "ocupacion_porcentaje": porcentaje_ocupacion,
        "ocupacion_display": ocupacion_display,
        "estado": estado,
        "productos_danados": productos_danados,
        "productos_caidos": productos_caidos,
        "advertencia": advertencia,
    }


def decode_frame(data: bytes | str) -> np.ndarray | None:
    if isinstance(data, str):
        if data.startswith("data:"):
            _, separator, data = data.partition(",")
            if not separator:
                return None
        try:
            data = base64.b64decode(data, validate=True)
        except ValueError:
            return None

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    return image


@asynccontextmanager
async def lifespan(_: FastAPI):
    global model
    if ENGINE_PATH.is_file():
        import torch

        if torch.cuda.is_available():
            from ultralytics import YOLO

            logger.info("CUDA disponible; cargando modelo TensorRT %s", ENGINE_PATH)
            model = YOLO(str(ENGINE_PATH), task="detect")
            yield
            model = None
            return
        logger.info("Hay un motor TensorRT, pero CUDA no está disponible; se usará el checkpoint .pt.")

    checkpoint_path = MODEL_PATH if MODEL_PATH.is_file() else MODEL_FALLBACK_PATH
    if not checkpoint_path.is_file():
        logger.error(
            "No se encontró models/best.pt ni models/best_v3.pt. "
            "La web estará disponible, pero la inferencia requiere uno de esos archivos."
        )
        yield
        return

    from ultralytics import YOLO

    if checkpoint_path == MODEL_FALLBACK_PATH:
        logger.warning("models/best.pt no existe; se utilizará %s", checkpoint_path)
    model = YOLO(str(checkpoint_path), task="detect")
    yield
    model = None


def predict_image(image: np.ndarray) -> Any:
    return model.predict(
        source=image,
        conf=PREDICT_CONFIDENCE,
        iou=PREDICT_IOU,
        imgsz=PREDICT_IMAGE_SIZE,
        verbose=False,
    )[0]


def extract_detections(result: Any) -> list[dict[str, Any]]:
    if result.boxes is None:
        return []

    detections = []
    coordinates = result.boxes.xyxy.cpu().numpy()
    class_ids = result.boxes.cls.cpu().numpy().astype(int)
    confidences = result.boxes.conf.cpu().numpy()
    for box, class_id, confidence in zip(coordinates, class_ids, confidences):
        class_id = int(class_id)
        confidence = float(confidence)
        if confidence < UMBRAL_POR_CLASE.get(class_id, PREDICT_CONFIDENCE):
            continue
        x1, y1, x2, y2 = (float(value) for value in box)
        detections.append({
            "class_id": class_id,
            "box": (x1, y1, x2, y2),
            "conf": confidence,
        })
    return detections


def detections_to_metrics(detections: list[dict[str, Any]]) -> dict[str, Any]:
    boxes = [
        (detection["class_id"], *detection["box"])
        for detection in detections
    ]
    return calculate_metrics(boxes)


def encode_audit_result(
    image: np.ndarray,
    result: Any,
) -> dict[str, Any]:
    detections = extract_detections(result)
    annotated = image.copy()
    for detection in detections:
        class_id = detection["class_id"]
        x1, y1, x2, y2 = detection["box"]
        label, color = CLASS_INFO.get(class_id, (f"CLASE {class_id}", (255, 255, 255)))
        cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        cv2.putText(
            annotated,
            f"{label} {detection['conf'] * 100:.0f}%",
            (int(x1), max(20, int(y1) - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )

    encoded_ok, jpeg = cv2.imencode(".jpg", annotated)
    if not encoded_ok:
        raise HTTPException(status_code=500, detail="No fue posible codificar la imagen anotada.")

    return {
        **detections_to_metrics(detections),
        "imagen_anotada": base64.b64encode(jpeg.tobytes()).decode("ascii"),
    }


app = FastAPI(title="Auditoría de Anaqueles", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/auditar")
async def auditar(file: UploadFile = File(...)) -> dict[str, Any]:
    image_bytes = await file.read()
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="El archivo recibido no es una imagen válida.")

    if model is None:
        raise HTTPException(status_code=503, detail="El modelo de auditoría no está disponible.")

    result = await run_in_threadpool(predict_image, image)
    return encode_audit_result(image, result)


@app.post("/auditar-lote")
async def auditar_lote(files: List[UploadFile] = File(...)) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="Selecciona al menos una imagen.")
    if model is None:
        raise HTTPException(status_code=503, detail="El modelo de auditoría no está disponible.")

    images = []
    filenames = []
    for index, file in enumerate(files, start=1):
        image_bytes = await file.read()
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(
                status_code=400,
                detail=f"El archivo {file.filename or index} no es una imagen válida.",
            )
        images.append(image)
        filenames.append(file.filename or f"imagen_{index}")

    predictions = await run_in_threadpool(
        model.predict,
        source=images,
        conf=PREDICT_CONFIDENCE,
        iou=PREDICT_IOU,
        imgsz=PREDICT_IMAGE_SIZE,
        verbose=False,
    )
    if len(predictions) != len(images):
        raise HTTPException(
            status_code=500,
            detail="La inferencia por lote no devolvió un resultado para cada imagen.",
        )

    results = []
    for filename, image, prediction in zip(filenames, images, predictions):
        results.append({
            "nombre_archivo": filename,
            **encode_audit_result(image, prediction),
        })
    return {"total_fotos": len(results), "resultados": results}


@app.websocket("/ws/auditar")
async def auditar_en_vivo(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            frame_data = await websocket.receive()
            if frame_data["type"] == "websocket.disconnect":
                break

            payload = frame_data.get("bytes")
            if payload is None:
                payload = frame_data.get("text")
            try:
                image = decode_frame(payload) if payload else None
            except (binascii.Error, ValueError, TypeError, cv2.error) as error:
                logger.warning("Se descartó un fotograma inválido: %s", error)
                await websocket.send_json({"error": "Fotograma JPEG/Base64 inválido; continúa enviando."})
                continue

            if image is None:
                await websocket.send_json({"error": "Fotograma JPEG/Base64 inválido; continúa enviando."})
                continue

            if model is None:
                await websocket.close(code=1011, reason="El modelo de auditoría no está disponible.")
                return

            result = await run_in_threadpool(predict_image, image)
            frame_height, frame_width = image.shape[:2]
            detections: list[dict[str, Any]] = []
            pixel_detections = extract_detections(result)
            for detection in pixel_detections:
                x1, y1, x2, y2 = detection["box"]
                class_id = detection["class_id"]
                detections.append({
                    "box": [
                        max(0.0, min(1.0, x1 / frame_width)),
                        max(0.0, min(1.0, y1 / frame_height)),
                        max(0.0, min(1.0, x2 / frame_width)),
                        max(0.0, min(1.0, y2 / frame_height)),
                    ],
                    "class_id": class_id,
                    "label": CLASS_INFO.get(class_id, (f"CLASE {class_id}",))[0],
                    "conf": detection["conf"],
                })

            metrics = detections_to_metrics(pixel_detections)
            await websocket.send_json({
                "detections": detections,
                **metrics,
                "producto_danado": metrics["productos_danados"],
                "producto_caido": metrics["productos_caidos"],
            })
    except WebSocketDisconnect:
        pass


app.mount("/", StaticFiles(directory=FRONTEND_PATH, html=True), name="frontend")


def generate_local_certificate() -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    addresses = {"127.0.0.1"}
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    addresses.update(
        address.strip()
        for address in os.environ.get("AUDIT_CERT_IPS", "").split(",")
        if address.strip()
    )
    san_entries = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname())]
    san_entries.extend(x509.IPAddress(ipaddress.ip_address(address)) for address in addresses)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Inventario Claro local"),
    ])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )

    KEY_PATH.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    CERT_PATH.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


if __name__ == "__main__":
    import uvicorn

    if os.environ.get("AUDIT_LOCAL_HTTP") == "1":
        uvicorn.run(app, host="127.0.0.1", port=8000)
    else:
        if not CERT_PATH.is_file() or not KEY_PATH.is_file():
            generate_local_certificate()
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            ssl_keyfile=str(KEY_PATH),
            ssl_certfile=str(CERT_PATH),
        )
