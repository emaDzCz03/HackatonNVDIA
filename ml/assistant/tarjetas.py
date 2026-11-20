"""Convierte la propuesta del modelo en una tarjeta editable para la app.

Criterios de campos:
- ENTRADA: producto, cantidad, costo por pieza y precio de venta son obligatorios.
  Si el producto ya existe y no se dijo precio o costo, se usan su precio actual y su
  último costo registrado (marcado como sugerido en la tarjeta).
  Proveedor ("Proveedor General") y forma de pago ("efectivo") tienen valor por defecto.
- CAMBIO_PRECIO: producto existente y precio nuevo.
- MERMA: producto existente y cantidad (no mayor a la existencia); motivo por defecto "dañado".
Lo que falta no se inventa: queda vacío en la tarjeta para que el usuario lo complete.
"""

import re

from . import bd
from .esquemas import Propuesta, RespuestaModelo

TITULOS = {"ENTRADA": "Entrada de mercancía", "CAMBIO_PRECIO": "Cambio de precio", "MERMA": "Merma o baja"}
OBLIGATORIOS = {
    "ENTRADA": ["producto", "cantidad", "costo_unitario", "precio_venta"],
    "CAMBIO_PRECIO": ["producto", "precio_venta"],
    "MERMA": ["producto", "cantidad"],
}
ETIQUETAS = {
    "producto": "el producto",
    "cantidad": "la cantidad",
    "costo_unitario": "el costo por pieza",
    "precio_venta": "el precio de venta",
}


def _opcion(producto: dict) -> dict:
    return {
        "id": producto["id"],
        "nombre": producto.get("nombre", ""),
        "existencia": producto.get("existencia", 0),
        "costo_promedio": producto.get("costo_promedio", 0),
        "precio_venta": producto.get("precio_venta", 0),
    }


def _lista(elementos: list[str]) -> str:
    return elementos[0] if len(elementos) == 1 else f"{', '.join(elementos[:-1])} y {elementos[-1]}"


def armar_tarjeta(propuesta: Propuesta) -> tuple[dict, str]:
    """Devuelve (tarjeta, mensaje para el chat)."""
    accion = propuesta.accion
    encontrado = bd.buscar_producto(propuesta.producto)

    sugeridos = []
    if accion == "ENTRADA":
        costo = propuesta.costo_unitario
        if costo is None and encontrado and encontrado.get("costo_promedio"):
            # Si no dijo el costo, se propone el último registrado en Firestore; el usuario lo puede cambiar.
            costo = encontrado["costo_promedio"]
            sugeridos.append("costo_unitario")
        campos = {
            "producto": encontrado["nombre"] if encontrado else propuesta.producto,
            "cantidad": propuesta.cantidad,
            "costo_unitario": costo,
            "precio_venta": propuesta.precio_venta or (encontrado or {}).get("precio_venta"),
            "proveedor": propuesta.proveedor or "Proveedor General",
            "forma_pago": propuesta.forma_pago or "efectivo",
        }
    elif accion == "CAMBIO_PRECIO":
        campos = {"producto": encontrado["nombre"] if encontrado else None, "precio_venta": propuesta.precio_venta}
    else:
        cantidad = propuesta.cantidad
        if propuesta.toda_la_existencia and encontrado:
            cantidad = encontrado.get("existencia", 0)
        campos = {
            "producto": encontrado["nombre"] if encontrado else None,
            "cantidad": cantidad,
            "motivo": propuesta.motivo or ("otro" if propuesta.toda_la_existencia else "dañado"),
        }

    faltantes = [campo for campo in OBLIGATORIOS[accion] if campos.get(campo) in (None, "")]
    tarjeta = {
        "accion": accion,
        "titulo": TITULOS[accion],
        "producto_id": encontrado["id"] if encontrado else None,
        "campos": campos,
        "faltantes": faltantes,
        "sugeridos": sugeridos,
        "productos": [_opcion(p) for p in bd.productos_activos()],
    }

    nombre = {"ENTRADA": "la entrada", "CAMBIO_PRECIO": "el cambio de precio", "MERMA": "la merma"}[accion]
    if faltantes:
        mensaje = f"Para {nombre} me falta {_lista([ETIQUETAS[c] for c in faltantes])}. Complétalo en la tarjeta."
    else:
        mensaje = f"Revisa {nombre} y presiona Confirmar para guardar{'lo' if accion == 'CAMBIO_PRECIO' else 'la'}."
    if accion != "ENTRADA" and not encontrado and propuesta.producto:
        mensaje = f"No encontré \"{propuesta.producto}\" en tu inventario. Elige el producto en la tarjeta."
    elif accion == "ENTRADA" and not encontrado and propuesta.producto:
        mensaje += f" {propuesta.producto[:1].upper()}{propuesta.producto[1:]} no está en tu inventario: se dará de alta como producto nuevo."
    if sugeridos:
        mensaje += f" Usé el último costo registrado (${campos['costo_unitario']:.2f}); cámbialo si esta vez te costó distinto."
    if accion == "MERMA" and propuesta.toda_la_existencia and encontrado and not campos["cantidad"]:
        mensaje = f"{encontrado['nombre']} ya no tiene piezas en existencia: no hay nada que dar de baja."
    elif accion == "MERMA" and propuesta.toda_la_existencia and encontrado:
        valor = campos["cantidad"] * encontrado.get("costo_promedio", 0)
        mensaje = (f"Atención: esto dará de baja todas las piezas de {encontrado['nombre']} "
                   f"({campos['cantidad']} piezas, ${valor:,.2f} a costo) y no se puede deshacer. "
                   "Revisa la tarjeta y confirma solo si estás seguro.")
    return tarjeta, mensaje


def salida_para_app(respuesta: RespuestaModelo) -> dict:
    """Arma lo que recibe la app: estado, mensaje del chat y tarjeta (si es una acción)."""
    salida = {"estado": respuesta.estado, "mensaje": respuesta.mensaje, "tarjeta": None}
    if respuesta.estado == "ACCION" and respuesta.propuesta:
        tarjeta, mensaje = armar_tarjeta(respuesta.propuesta)
        consejo = respuesta.mensaje.strip()
        # En una acción los números los calcula Python (tarjeta); un consejo con cifras se descarta.
        if re.search(r"[$%]|\d", consejo):
            consejo = ""
        salida.update(tarjeta=tarjeta, mensaje=f"{mensaje}\n{consejo}" if consejo else mensaje)
    return salida
