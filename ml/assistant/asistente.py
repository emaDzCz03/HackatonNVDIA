import json
import logging
import os
import random
from pathlib import Path

import instructor
from dotenv import load_dotenv
from instructor.core.exceptions import InstructorError
from openai import OpenAI, OpenAIError

from .esquemas import RespuestaModelo

ASSISTANT_DIR = Path(__file__).resolve().parent
load_dotenv(ASSISTANT_DIR.parents[1] / ".env")

PROMPT_MAESTRO = (ASSISTANT_DIR / "PROMPT.md").read_text(encoding="utf-8").strip()
# Python sugiere un tema al azar en cada mensaje para que los consejos no se repitan.
# "Fiado" no está aquí: solo aplica cuando el usuario lo menciona.
TEMAS_TIP = ["Cobros digitales", "Caducidad", "Proveedores", "Precios a la vista", "Efectivo", "Acomodo", "Conteo"]

logger = logging.getLogger("uvicorn.error")


def _proveedor(nombre: str, base_url: str, api_key: str, modelo: str, timeout: float,
               extra_body: dict | None = None, reintentos: int = 1) -> dict:
    cliente = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=reintentos)
    return {
        "nombre": nombre,
        "modelo": modelo,
        "cliente": instructor.from_openai(cliente, mode=instructor.Mode.JSON),
        "extra_body": extra_body,
    }


# Se prueban en orden. NVIDIA (gratuito) a veces deja una petición en cola: si no responde
# en 8 s, se pasa directo a DeepSeek en OpenRouter (sin reintentar NVIDIA, para no hacer esperar).
# Sin llave de OpenRouter, NVIDIA se reintenta una vez.
PROVEEDORES = []
if os.getenv("NVIDIA_API_KEY"):
    PROVEEDORES.append(_proveedor(
        "NVIDIA",
        os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        os.getenv("NVIDIA_API_KEY"),
        os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
        timeout=8,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        reintentos=0 if os.getenv("OPENROUTER_API_KEY") else 1,
    ))
if os.getenv("OPENROUTER_API_KEY"):
    PROVEEDORES.append(_proveedor(
        "OpenRouter",
        os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        os.getenv("OPENROUTER_API_KEY"),
        os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash"),
        timeout=12,
        # DeepSeek razona por defecto: gasta ~750 tokens y ~16 s, y a veces no le alcanza para el JSON.
        extra_body={"reasoning": {"enabled": False}},
        reintentos=0,
    ))


def _mensaje_usuario(mensaje: str, contexto: str | None = None, tema: str | None = None) -> dict[str, str]:
    contenido = f"<mensaje_usuario>\n{mensaje}\n</mensaje_usuario>"
    if tema:
        contenido = f"[TEMA SUGERIDO PARA EL TIP]\n{tema}\n\n{contenido}"
    if contexto is not None:
        contenido = f"[CONTEXTO]\n{contexto or 'Sin datos registrados.'}\n\n{contenido}"
    return {"role": "user", "content": contenido}


def _cargar_few_shot() -> list[dict[str, str]]:
    datos = json.loads((ASSISTANT_DIR / "ejemplos.json").read_text(encoding="utf-8"))
    mensajes = []
    for ejemplo in datos["ejemplos"]:
        respuesta = RespuestaModelo.model_validate(ejemplo["asistente"])
        mensajes.append(_mensaje_usuario(ejemplo["usuario"], datos["contextos"][ejemplo["contexto"]], ejemplo.get("tema")))
        mensajes.append({"role": "assistant", "content": respuesta.model_dump_json(exclude_none=True)})
    return mensajes


FEW_SHOT = _cargar_few_shot()


def responder(mensaje: str, contexto: str = "", historial: list[dict[str, str]] | None = None) -> tuple[RespuestaModelo, str]:
    """Devuelve (respuesta, nombre del proveedor que respondió).

    historial: turnos previos [{"rol": "usuario"|"asistente", "texto": ...}], del más viejo al más nuevo.
    """
    previos = []
    for turno in historial or []:
        if turno["rol"] == "usuario":
            previos.append(_mensaje_usuario(turno["texto"]))
        else:
            previos.append({"role": "assistant", "content": json.dumps({"estado": "CONSULTA", "mensaje": turno["texto"]}, ensure_ascii=False)})

    mensajes = [
        {"role": "system", "content": PROMPT_MAESTRO},
        *FEW_SHOT,
        *previos,
        _mensaje_usuario(mensaje, contexto, random.choice(TEMAS_TIP)),
    ]
    if not PROVEEDORES:
        # Sin llaves el servidor arranca igual (la auditoría de anaquel sigue funcionando).
        raise OpenAIError("No hay NVIDIA_API_KEY ni OPENROUTER_API_KEY en .env.")
    error = None
    for proveedor in PROVEEDORES:
        try:
            respuesta = proveedor["cliente"].chat.completions.create(
                model=proveedor["modelo"],
                response_model=RespuestaModelo,
                max_retries=1,
                messages=mensajes,
                temperature=0.2,
                top_p=0.95,
                max_tokens=1024,
                extra_body=proveedor["extra_body"],
            )
            return respuesta, f"{proveedor['nombre']} ({proveedor['modelo']})"
        except (OpenAIError, InstructorError) as falla:
            logger.warning("[asistente] Falló %s: %s. Pruebo el siguiente proveedor.", proveedor["nombre"], type(falla).__name__)
            error = falla
    raise error
