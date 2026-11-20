"""Lectura de Firestore para el asistente. Este módulo solo lee: nunca escribe (ver escritura.py)."""

import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

RAIZ = Path(__file__).resolve().parents[2]
CREDENCIALES = Path(os.getenv("FIREBASE_CREDENTIALS", RAIZ / "serviceAccountKey.json"))
COMISION_TARJETA = 0.05
SEGUNDOS_CACHE = 30
PALABRAS_GANCHO = ("refresco", "coca", "cola", "cigarro", "botana", "sabritas", "papas", "pan", "leche", "cerveza")

logger = logging.getLogger(__name__)
_cache: dict[str, tuple[float, object]] = {}
_candado_init = threading.Lock()


def db():
    # El arranque precalienta Firestore en otro hilo: sin candado, la primera petición
    # podía inicializar la app dos veces y fallar con "The default Firebase app already exists".
    with _candado_init:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(CREDENCIALES))
    return firestore.client()


def _cacheado(clave: str, leer):
    guardado = _cache.get(clave)
    if guardado and time.monotonic() - guardado[0] < SEGUNDOS_CACHE:
        return guardado[1]
    valor = leer()
    _cache[clave] = (time.monotonic(), valor)
    return valor


def invalidar_cache() -> None:
    _cache.clear()


def productos_activos() -> list[dict]:
    """Productos activos con su id de documento en el campo "id"."""
    def leer():
        consulta = db().collection("productos").where(filter=FieldFilter("activo", "==", True))
        return [{"id": doc.id, **doc.to_dict()} for doc in consulta.stream()]
    return _cacheado("productos", leer)


def _normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return " ".join(sin_acentos.split())


def buscar_producto(texto: str | None) -> dict | None:
    """Encuentra un producto por nombre o alias ("cocas" -> Refresco Cola 600ml)."""
    if not texto:
        return None
    palabras = {p.removesuffix("es") if p.endswith("es") and len(p) > 4 else p.removesuffix("s")
                for p in re.findall(r"[a-z0-9]+", _normalizar(texto))}
    for producto in productos_activos():
        for nombre in [producto.get("nombre", ""), *producto.get("alias", [])]:
            claves = set(re.findall(r"[a-z0-9]+", _normalizar(nombre)))
            # Coincide si todas las palabras del alias están en el texto, o si el texto es una palabra del nombre.
            if claves and (claves <= palabras or (len(palabras) == 1 and palabras <= claves)):
                return producto
    return None


def buscar_por_nombre(nombre: str) -> dict | None:
    """Coincidencia exacta (sin acentos ni mayúsculas) para no duplicar productos al dar de alta."""
    clave = _normalizar(nombre)
    return next((p for p in productos_activos() if _normalizar(p.get("nombre", "")) == clave), None)


def lista_resurtido() -> dict:
    """Productos bajo el mínimo con cuánto pedir (hasta el doble del mínimo) y costo estimado."""
    renglones = []
    for p in productos_activos():
        existencia, minimo = p.get("existencia", 0), p.get("existencia_minima", 0)
        if existencia >= minimo:
            continue
        pedir = max(minimo * 2 - existencia, 1)
        renglones.append({
            "producto": p.get("nombre", "Sin nombre"),
            "existencia": existencia,
            "minimo": minimo,
            "pedir": pedir,
            "costo_estimado": round(pedir * p.get("costo_promedio", 0), 2),
            "gancho": _es_gancho(p),
        })
    # Primero los productos gancho (traen clientes) y luego los más agotados.
    renglones.sort(key=lambda r: (not r["gancho"], r["existencia"] / r["minimo"] if r["minimo"] else 0))
    return {"productos": renglones, "total_estimado": round(sum(r["costo_estimado"] for r in renglones), 2)}


def _fecha(valor) -> str:
    return valor.strftime("%Y-%m-%d") if isinstance(valor, datetime) else "sin fecha"


def _lectura_margen(margen: float) -> str:
    if margen <= 0:
        return "pierde dinero"
    if margen < 15:
        return "muy bajo"
    if margen < 20:
        return "bajo"
    if margen <= 30:
        return "en el promedio"
    return "arriba del promedio"


def _es_gancho(producto: dict) -> bool:
    texto = " ".join([producto.get("nombre", ""), *producto.get("alias", [])]).lower()
    return any(palabra in texto for palabra in PALABRAS_GANCHO)


def _seccion_productos(productos: list[dict]) -> list[str]:
    if not productos:
        return ["PRODUCTOS", "- Ninguno registrado."]

    valores = [p.get("existencia", 0) * p.get("costo_promedio", 0) for p in productos]
    total = sum(valores)
    lineas, bajo_minimo, margen_bajo = ["PRODUCTOS"], [], []

    for producto, valor in zip(productos, valores):
        nombre = producto.get("nombre", "Sin nombre")
        costo = producto.get("costo_promedio", 0)
        precio = producto.get("precio_venta", 0)
        existencia = producto.get("existencia", 0)
        minimo = producto.get("existencia_minima", 0)
        gancho = _es_gancho(producto)

        partes = [
            nombre,
            f"alias: {', '.join(producto.get('alias', [])) or 'ninguno'}",
            f"existencia {existencia} (mínimo {minimo})",
            f"costo ${costo:.2f}",
            f"precio ${precio:.2f}",
        ]
        if precio > 0:
            ganancia_digital = precio - costo
            ganancia_tarjeta = precio * (1 - COMISION_TARJETA) - costo
            margen_tarjeta = ganancia_tarjeta / precio * 100
            partes += [
                f"ganancia digital ${ganancia_digital:.2f} ({ganancia_digital / precio * 100:.2f} %)",
                f"ganancia con tarjeta ${ganancia_tarjeta:.2f} ({margen_tarjeta:.2f} %, {_lectura_margen(margen_tarjeta)})",
            ]
            if margen_tarjeta < 20:
                sugerido = costo * 10 / 7
                margen_bajo.append(f"{nombre} ({margen_tarjeta:.2f} %; precio para 25 % con tarjeta: ${sugerido:.2f})")
        partes.append(f"en mercancía ${valor:.2f} ({valor / total * 100:.2f} % del total)" if total else "en mercancía $0.00")
        if gancho:
            partes.append("producto gancho")
        lineas.append("- " + " | ".join(partes))

        if existencia < minimo:
            urgente = ", urgente por ser producto gancho" if gancho else ""
            bajo_minimo.append(f"{nombre} ({existencia} de {minimo}{urgente})")

    lineas += [
        "",
        "RESUMEN",
        f"- Dinero total en mercancía: ${total:.2f}",
        f"- Bajo el mínimo: {'; '.join(bajo_minimo) or 'ninguno'}",
        f"- Margen con tarjeta menor a 20 %: {'; '.join(margen_bajo) or 'ninguno'}",
    ]
    return lineas


def _seccion_entregas(db, limite: int = 5) -> list[str]:
    consulta = db.collection("entregas").order_by("fecha_creacion", direction=firestore.Query.DESCENDING).limit(limite)
    lineas = ["ENTREGAS RECIENTES"]
    for entrega in consulta.stream():
        datos = entrega.to_dict()
        detalle = db.collection("detalle_entregas").where(filter=FieldFilter("entrega_id", "==", entrega.id)).stream()
        productos = ", ".join(
            f"{d.get('cantidad', 0)} × {d.get('nombre_snapshot', 'producto')} a ${d.get('costo_unitario', 0):.2f}"
            for d in (item.to_dict() for item in detalle)
        )
        lineas.append(
            f"- {_fecha(datos.get('fecha_creacion'))} | {datos.get('proveedor', 'sin proveedor')} | "
            f"{datos.get('forma_pago', 'sin forma de pago')} | {datos.get('estado', '')} | "
            f"total ${datos.get('total', 0):.2f} | {productos or 'sin detalle'}"
        )
    return lineas if len(lineas) > 1 else lineas + ["- Ninguna registrada."]


def _seccion_auditoria(db) -> list[str]:
    consulta = db.collection("auditorias").order_by("fecha_creacion", direction=firestore.Query.DESCENDING).limit(1)
    for auditoria in consulta.stream():
        a = auditoria.to_dict()
        return [
            "ÚLTIMA AUDITORÍA DE ANAQUEL",
            f"- {_fecha(a.get('fecha_creacion'))} | {a.get('estado', '')} | ocupación {a.get('ocupacion_porcentaje', 0)} % | "
            f"faltantes {a.get('faltantes_estimados', 0)} | dañados {a.get('productos_danados', 0)} | "
            f"caídos {a.get('productos_caidos', 0)}",
        ]
    return ["ÚLTIMA AUDITORÍA DE ANAQUEL", "- Ninguna registrada."]


def contexto_inventario() -> str:
    """Devuelve el inventario como texto para [CONTEXTO], con los cálculos ya hechos."""
    def leer():
        cliente = db()
        return "\n".join([
            *_seccion_productos(productos_activos()), "",
            *_seccion_entregas(cliente), "",
            *_seccion_auditoria(cliente),
        ])
    try:
        return _cacheado("contexto", leer)
    except Exception:
        logger.exception("No se pudo leer Firestore para el asistente.")
        return "No se pudo leer la base de datos en este momento."
