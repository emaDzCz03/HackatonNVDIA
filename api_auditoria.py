import base64
import binascii
import ipaddress
import logging
import os
import socket
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
import time
from typing import Any, List, Literal

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from instructor.core.exceptions import InstructorError
from ml.assistant.asistente import responder
from ml.assistant.bd import contexto_inventario, lista_resurtido
from ml.assistant.escritura import confirmar
from ml.assistant.esquemas import Confirmacion
from ml.assistant.tarjetas import salida_para_app


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
identificador: Any = None
logger = logging.getLogger(__name__)


def cargar_identificador() -> None:
    """Carga la galería de marcas (models/galeria) en segundo plano; es opcional."""
    global identificador
    try:
        from ml.vision.identificar import GALERIA, Identificador

        if GALERIA.is_dir():
            identificador = Identificador()
            logger.info("Identificador de marcas listo (%d fotos de referencia).", len(identificador.marcas))
    except Exception:
        logger.exception("No se pudo cargar el identificador de marcas; se omite.")


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
    # Abre la conexión a Firestore en segundo plano para que la primera pregunta no tarde.
    threading.Thread(target=contexto_inventario, daemon=True).start()
    threading.Thread(target=cargar_identificador, daemon=True).start()
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
    marcas = identificar_marcas(image, detections)
    annotated = image.copy()
    for detection in detections:
        class_id = detection["class_id"]
        x1, y1, x2, y2 = detection["box"]
        label, color = CLASS_INFO.get(class_id, (f"CLASE {class_id}", (255, 255, 255)))
        if detection.get("marca"):
            label = detection["marca"]
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
        **marcas,
        "imagen_anotada": base64.b64encode(jpeg.tobytes()).decode("ascii"),
    }


def identificar_marcas(image: np.ndarray, detections: list[dict[str, Any]]) -> dict[str, Any]:
    """Pone "marca" en cada producto reconocido y resume cuántos hay de cada una."""
    if identificador is None:
        return {}
    productos = [d for d in detections if d["class_id"] == 0]
    recortes = []
    for d in productos:
        x1, y1, x2, y2 = (int(v) for v in d["box"])
        recortes.append(image[max(0, y1):y2, max(0, x1):x2])
    validos = [(d, r) for d, r in zip(productos, recortes) if r.size and min(r.shape[:2]) >= 12]
    conteo: dict[str, int] = {}
    for (d, _), (marca, _score) in zip(validos, identificador.identificar([r for _, r in validos])):
        if marca:
            d["marca"] = marca
            conteo[marca] = conteo.get(marca, 0) + 1
    return {
        "productos_identificados": dict(sorted(conteo.items(), key=lambda kv: -kv[1])),
        "productos_sin_identificar": len(productos) - sum(conteo.values()),
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


log_asistente = logging.getLogger("uvicorn.error")


class Turno(BaseModel):
    rol: Literal["usuario", "asistente"]
    texto: str = Field(max_length=2000)


class MensajeAsistente(BaseModel):
    mensaje: str = Field(min_length=1, max_length=1000)
    historial: list[Turno] = Field(default_factory=list, max_length=6)


@app.post("/asistente")
async def asistente(peticion: MensajeAsistente) -> dict[str, Any]:
    inicio = time.perf_counter()
    try:
        contexto = await run_in_threadpool(contexto_inventario)
        segundos_bd = time.perf_counter() - inicio
        historial = [turno.model_dump() for turno in peticion.historial]
        respuesta, proveedor = await run_in_threadpool(responder, peticion.mensaje, contexto, historial)
        segundos_modelo = time.perf_counter() - inicio - segundos_bd
    except (OpenAIError, InstructorError):
        logger.exception("Falló la llamada al modelo del asistente.")
        raise HTTPException(status_code=503, detail="El asistente no está disponible. Intenta de nuevo.")
    except Exception:
        logger.exception("Error inesperado en /asistente.")
        raise HTTPException(status_code=503, detail="El asistente no pudo responder en este momento. Intenta de nuevo.")

    try:
        salida = await run_in_threadpool(salida_para_app, respuesta)
    except Exception:
        # Armar la tarjeta lee Firestore; si falla, se devuelve el texto sin tarjeta en vez de un 500.
        logger.exception("No se pudo armar la tarjeta del asistente.")
        salida = {"estado": respuesta.estado, "tarjeta": None,
                  "mensaje": "No pude consultar tu inventario para armar la tarjeta. Intenta de nuevo en un minuto."}

    log_asistente.info(
        "[asistente] %r | total %.1f s (Firestore %.1f s, modelo %.1f s) | %s | %s",
        peticion.mensaje, time.perf_counter() - inicio, segundos_bd, segundos_modelo, proveedor,
        respuesta.model_dump_json(exclude_none=True),
    )
    return salida


@app.get("/resurtido")
async def resurtido() -> dict[str, Any]:
    """Lista de compras sin pasar por el modelo: instantánea y sin riesgo de inventar cifras."""
    try:
        return await run_in_threadpool(lista_resurtido)
    except Exception:
        logger.exception("No se pudo leer Firestore para la lista de resurtido.")
        raise HTTPException(status_code=503, detail="No pude consultar tu inventario. Intenta de nuevo en un minuto.")


@app.post("/confirmar")
async def confirmar_tarjeta(datos: Confirmacion) -> dict[str, str]:
    try:
        mensaje = await run_in_threadpool(confirmar, datos)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        logger.exception("Falló la escritura en Firestore.")
        raise HTTPException(status_code=503, detail="No pude guardar en la base de datos. Intenta de nuevo.")
    log_asistente.info("[confirmar] %s", datos.model_dump_json())
    return {"mensaje": mensaje}


app.mount("/", StaticFiles(directory=FRONTEND_PATH, html=True), name="frontend")


def _direcciones_locales() -> set[str]:
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
    return addresses


def _certificado_actualizado() -> bool:
    """Verifica que cert.pem y key.pem existan, sigan vigentes e incluyan las IPs actuales."""
    if not CERT_PATH.is_file() or not KEY_PATH.is_file():
        return False
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(CERT_PATH.read_bytes())
        if cert.not_valid_after_utc <= datetime.now(timezone.utc):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        cert_ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        return _direcciones_locales() <= cert_ips
    except Exception:
        return False


def generate_local_certificate() -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    addresses = _direcciones_locales()
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
        if not _certificado_actualizado():
            generate_local_certificate()
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            ssl_keyfile=str(KEY_PATH),
            ssl_certfile=str(CERT_PATH),
        )
