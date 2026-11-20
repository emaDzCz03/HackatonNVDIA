"""Escritura en Firestore. Solo se llama desde POST /confirmar, cuando el usuario
presiona "Confirmar" en la tarjeta. El modelo nunca llama a este módulo."""

import re
import unicodedata
import uuid

from firebase_admin import firestore

from .bd import buscar_por_nombre, db, invalidar_cache
from .esquemas import CambioPrecio, Entrada, Merma

AHORA = firestore.SERVER_TIMESTAMP


def _id_producto(nombre: str) -> str:
    base = unicodedata.normalize("NFKD", nombre.lower()).encode("ascii", "ignore").decode()
    return f"prod_{re.sub(r'[^a-z0-9]+', '_', base).strip('_')[:30]}_{uuid.uuid4().hex[:4]}"


def _producto(transaction, cliente, producto_id: str):
    referencia = cliente.collection("productos").document(producto_id)
    documento = referencia.get(transaction=transaction)
    if not documento.exists:
        raise ValueError("Ese producto ya no existe en el inventario.")
    return referencia, documento.to_dict()


def registrar_entrada(entrada: Entrada) -> str:
    cliente = db()
    if not entrada.producto_id and (existente := buscar_por_nombre(entrada.producto)):
        # El usuario escribió a mano el nombre de un producto que ya existe: se suma a ese, no se duplica.
        entrada = entrada.model_copy(update={"producto_id": existente["id"], "producto": existente["nombre"]})

    @firestore.transactional
    def guardar(transaction):
        if entrada.producto_id:
            referencia, actual = _producto(transaction, cliente, entrada.producto_id)
            existencia = actual.get("existencia", 0)
            nueva_existencia = existencia + entrada.cantidad
            costo_promedio = (existencia * actual.get("costo_promedio", 0) + entrada.cantidad * entrada.costo_unitario) / nueva_existencia
            transaction.update(referencia, {
                "existencia": nueva_existencia,
                "costo_promedio": round(costo_promedio, 2),
                "precio_venta": entrada.precio_venta,
                "actualizado_en": AHORA,
            })
        else:
            referencia = cliente.collection("productos").document(_id_producto(entrada.producto))
            nueva_existencia = entrada.cantidad
            transaction.set(referencia, {
                "nombre": entrada.producto,
                "categoria": "Sin categoría",
                "alias": [entrada.producto.lower()],
                "precio_venta": entrada.precio_venta,
                "costo_promedio": entrada.costo_unitario,
                "existencia": entrada.cantidad,
                "existencia_minima": 0,
                "activo": True,
                "actualizado_en": AHORA,
            })

        entrega = cliente.collection("entregas").document()
        transaction.set(entrega, {
            "proveedor": entrada.proveedor,
            "forma_pago": entrada.forma_pago,
            "estado": "confirmada",
            "origen": "chatbot",
            "imagen_url": None,
            "total": round(entrada.cantidad * entrada.costo_unitario, 2),
            "fecha_creacion": AHORA,
        })
        margen = entrada.precio_venta - entrada.costo_unitario
        transaction.set(cliente.collection("detalle_entregas").document(), {
            "entrega_id": entrega.id,
            "producto_id": referencia.id,
            "nombre_snapshot": entrada.producto,
            "cantidad": entrada.cantidad,
            "costo_unitario": entrada.costo_unitario,
            "precio_venta": entrada.precio_venta,
            "margen": round(margen, 2),
            "margen_porcentaje": round(margen / entrada.precio_venta * 100, 2),
        })
        return nueva_existencia

    nueva_existencia = guardar(cliente.transaction())
    invalidar_cache()
    return f"Guardé la entrada de {entrada.cantidad} {entrada.producto}. Ahora tienes {nueva_existencia} piezas."


def cambiar_precio(cambio: CambioPrecio) -> str:
    cliente = db()

    @firestore.transactional
    def guardar(transaction):
        referencia, actual = _producto(transaction, cliente, cambio.producto_id)
        transaction.update(referencia, {"precio_venta": cambio.precio_venta, "actualizado_en": AHORA})
        return actual.get("precio_venta", 0)

    anterior = guardar(cliente.transaction())
    invalidar_cache()
    return f"Guardé el nuevo precio de {cambio.producto}: de ${anterior:.2f} a ${cambio.precio_venta:.2f}."


def registrar_merma(merma: Merma) -> str:
    cliente = db()

    @firestore.transactional
    def guardar(transaction):
        referencia, actual = _producto(transaction, cliente, merma.producto_id)
        existencia = actual.get("existencia", 0)
        if merma.cantidad > existencia:
            raise ValueError(f"Solo tienes {existencia} piezas de {merma.producto}; no puedo dar de baja {merma.cantidad}.")
        costo = actual.get("costo_promedio", 0)
        transaction.update(referencia, {"existencia": existencia - merma.cantidad, "actualizado_en": AHORA})
        transaction.set(cliente.collection("mermas").document(), {
            "producto_id": referencia.id,
            "nombre_snapshot": merma.producto,
            "cantidad": merma.cantidad,
            "motivo": merma.motivo,
            "costo_unitario": costo,
            "perdida": round(merma.cantidad * costo, 2),
            "origen": "chatbot",
            "fecha_creacion": AHORA,
        })
        return existencia - merma.cantidad, merma.cantidad * costo

    restante, perdida = guardar(cliente.transaction())
    invalidar_cache()
    return f"Guardé la baja de {merma.cantidad} {merma.producto} ({merma.motivo}). Perdiste ${perdida:.2f}; te quedan {restante} piezas."


ACCIONES = {"ENTRADA": registrar_entrada, "CAMBIO_PRECIO": cambiar_precio, "MERMA": registrar_merma}


def confirmar(datos: Entrada | CambioPrecio | Merma) -> str:
    return ACCIONES[datos.accion](datos)
