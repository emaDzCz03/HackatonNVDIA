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
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool


MODEL_PATH = Path(__file__).resolve().parent / "models" / "best.pt"
FRONTEND_PATH = Path(__file__).resolve().parent / "frontend"
CERT_PATH = Path(__file__).resolve().parent / "cert.pem"
KEY_PATH = Path(__file__).resolve().parent / "key.pem"
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
) -> dict[str, int | float | str]:
    botellas_ok = sum(class_id == 0 for class_id, *_ in detections)
    espacios = [item for item in detections if item[0] == 1]
    espacios_vacios = len(espacios)
    productos_danados = sum(class_id == 2 for class_id, *_ in detections)
    productos_caidos = sum(class_id == 3 for class_id, *_ in detections)

    bottle_widths = [x2 - x1 for class_id, x1, _, x2, _ in detections if class_id == 0]
    median_bottle_width = median(bottle_widths) if bottle_widths else None
    if median_bottle_width:
        faltantes_estimados = sum(
            max(1, round((x2 - x1) / median_bottle_width))
            for _, x1, _, x2, _ in espacios
        )
    else:
        faltantes_estimados = espacios_vacios

    total_estimado = botellas_ok + faltantes_estimados
    ocupacion_porcentaje = (
        round((botellas_ok / total_estimado) * 100, 1) if botellas_ok else 0
    )

    if botellas_ok == 0:
        estado = "ESTANTE VACIO"
    elif (
        ocupacion_porcentaje < 75
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
        "ocupacion_porcentaje": ocupacion_porcentaje,
        "estado": estado,
        "productos_danados": productos_danados,
        "productos_caidos": productos_caidos,
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
    if not MODEL_PATH.is_file():
        logger.error(
            f"No se encontró el modelo en {MODEL_PATH}. "
            "La web estará disponible, pero la inferencia requiere models/best.pt."
        )
        yield
        return

    from ultralytics import YOLO

    model = YOLO(str(MODEL_PATH))
    yield
    model = None


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
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="El archivo recibido no es una imagen válida.")

    if model is None:
        raise HTTPException(status_code=503, detail="El modelo de auditoría no está disponible.")

    result = (await run_in_threadpool(model.predict, source=image, verbose=False))[0]
    annotated = image.copy()
    detections: list[tuple[int, float, float, float, float]] = []

    if result.boxes is not None:
        coordinates = result.boxes.xyxy.cpu().numpy()
        class_ids = result.boxes.cls.cpu().numpy().astype(int)
        for box, class_id in zip(coordinates, class_ids):
            x1, y1, x2, y2 = (float(value) for value in box)
            detections.append((class_id, x1, y1, x2, y2))

            label, color = CLASS_INFO.get(class_id, (f"CLASE {class_id}", (255, 255, 255)))
            top_left = (int(x1), int(y1))
            bottom_right = (int(x2), int(y2))
            cv2.rectangle(annotated, top_left, bottom_right, color, 2)
            text_y = max(20, int(y1) - 8)
            cv2.putText(
                annotated,
                label,
                (int(x1), text_y),
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
        **calculate_metrics(detections),
        "imagen_anotada": base64.b64encode(jpeg.tobytes()).decode("ascii"),
    }


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

            result = (await run_in_threadpool(
                model.predict,
                source=image,
                conf=0.28,
                imgsz=640,
                verbose=False,
            ))[0]
            frame_height, frame_width = image.shape[:2]
            detections: list[dict[str, Any]] = []
            metric_boxes: list[tuple[int, float, float, float, float]] = []

            if result.boxes is not None:
                coordinates = result.boxes.xyxy.cpu().numpy()
                class_ids = result.boxes.cls.cpu().numpy().astype(int)
                confidences = result.boxes.conf.cpu().numpy()
                for box, class_id, confidence in zip(coordinates, class_ids, confidences):
                    x1, y1, x2, y2 = (float(value) for value in box)
                    metric_boxes.append((class_id, x1, y1, x2, y2))
                    label = CLASS_INFO.get(class_id, (f"CLASE {class_id}",))[0]
                    detections.append({
                        "box": [
                            max(0.0, min(1.0, x1 / frame_width)),
                            max(0.0, min(1.0, y1 / frame_height)),
                            max(0.0, min(1.0, x2 / frame_width)),
                            max(0.0, min(1.0, y2 / frame_height)),
                        ],
                        "class_id": int(class_id),
                        "label": label,
                        "conf": float(confidence),
                    })

            metrics = calculate_metrics(metric_boxes)
            await websocket.send_json({
                "detections": detections,
                "botellas_ok": metrics["botellas_ok"],
                "espacios_vacios": metrics["espacios_vacios"],
                "faltantes_estimados": metrics["faltantes_estimados"],
                "producto_danado": metrics["productos_danados"],
                "producto_caido": metrics["productos_caidos"],
                "ocupacion_porcentaje": metrics["ocupacion_porcentaje"],
                "estado": metrics["estado"],
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
