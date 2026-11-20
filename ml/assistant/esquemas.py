"""Contratos de datos del asistente (Pydantic).

- RespuestaModelo: lo que debe devolver el LLM. Todo campo de la propuesta es
  opcional: si el usuario no lo dijo, el modelo lo deja en null.
- Entrada / CambioPrecio / Merma: lo que la app envía al confirmar. Aquí todos
  los campos obligatorios se exigen antes de escribir en Firestore.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

FormaPago = Literal["efectivo", "transferencia", "tarjeta", "credito"]
Motivo = Literal["dañado", "caducado", "robo", "otro"]


class Propuesta(BaseModel):
    accion: Literal["ENTRADA", "CAMBIO_PRECIO", "MERMA"]
    producto: str | None = Field(None, description="Nombre del producto como lo dijo el usuario")
    cantidad: int | None = Field(None, description="Piezas")
    costo_unitario: float | None = Field(None, description="Costo por pieza")
    precio_venta: float | None = Field(None, description="Precio de venta por pieza")
    proveedor: str | None = None
    forma_pago: FormaPago | None = None
    motivo: Motivo | None = None
    toda_la_existencia: bool | None = Field(None, description="True si pide borrar o dar de baja todo el producto")


class RespuestaModelo(BaseModel):
    estado: Literal["CONSULTA", "ACCION", "FUERA_DE_ALCANCE"]
    mensaje: str
    propuesta: Propuesta | None = None


class Entrada(BaseModel):
    accion: Literal["ENTRADA"]
    producto_id: str | None = None
    producto: str = Field(min_length=1)
    cantidad: int = Field(gt=0)
    costo_unitario: float = Field(gt=0)
    precio_venta: float = Field(gt=0)
    proveedor: str = Field("Proveedor General", min_length=1)
    forma_pago: FormaPago = "efectivo"


class CambioPrecio(BaseModel):
    accion: Literal["CAMBIO_PRECIO"]
    producto_id: str = Field(min_length=1)
    producto: str
    precio_venta: float = Field(gt=0)


class Merma(BaseModel):
    accion: Literal["MERMA"]
    producto_id: str = Field(min_length=1)
    producto: str
    cantidad: int = Field(gt=0)
    motivo: Motivo = "dañado"


Confirmacion = Annotated[Entrada | CambioPrecio | Merma, Field(discriminator="accion")]
