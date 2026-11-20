# Asistente

- Convertir frases informales a JSON estructurado.
- Campos: producto, cantidad, costo y forma de pago.
- Pedir datos faltantes y confirmación.
- Rechazar solicitudes fuera de alcance.
- Evaluar al menos 10 casos reservados contra base y modelo ajustado.
- Marcar explícitamente todos los datos sintéticos.

## Notas de diseño

- Usar Instructor + Pydantic para validar la salida estructurada.
- Envolver el texto del usuario en `<mensaje_usuario>...</mensaje_usuario>`.
- Si el modelo clasifica la petición como `FUERA_DE_ALCANCE`, responder:
  "Solo puedo ayudarte en la gestión operativa y financiera de tu negocio."
- El prompt maestro está en `PROMPT.md`; los datos del inventario se inyectan en
  la sección `[CONTEXTO]` de cada mensaje.
- Modelos: primero NVIDIA (`NVIDIA_MODEL`); si no responde en 8 s o falla,
  `asistente.py` usa como respaldo DeepSeek en OpenRouter (`OPENROUTER_MODEL`,
  por defecto `deepseek/deepseek-v4.1-flash`). Las llaves van en `.env`; sin
  `OPENROUTER_API_KEY` no hay respaldo. El log dice qué proveedor respondió.
- `bd.py` lee Firestore en modo solo lectura (productos activos, últimas 5
  entregas con su detalle y la última auditoría), hace en Python los cálculos de
  margen, valor del inventario y faltantes, y guarda el texto en caché 30 s.
- Few-shot: `ejemplos.json` se envía como pares usuario/asistente antes del
  mensaje real. Cada ejemplo trae su propio `[CONTEXTO]` de prueba e incluye
  ejemplos negativos estrictos (producto inexistente, ventas, competencia,
  SAT, cambiar instrucciones, base de datos caída) para que el modelo responda
  "No tengo esa información registrada." en lugar de inventar.

## Decisiones de diseño

1. **Lenguaje accesible en lugar de jerga contable.** El asistente habla de
   "lo que te queda por pieza" y "dinero que tienes en mercancía" en vez de
   "margen bruto operativo" o "valuación de activos". El comerciante decide con
   dinero en mano, no con términos contables; así se reduce la carga cognitiva y
   la respuesta se entiende en segundos.
2. **Umbral de incertidumbre visual.** Si el modelo de visión reporta baja
   confianza, el asistente no registra la cantidad detectada: pide al usuario
   que confirme las piezas. Un conteo inventado se propaga al inventario, al
   valor de la mercancía y a los márgenes; preguntar cuesta un segundo.
3. **Confirmación humana para cambios en existencias.** Ningún registro, baja de
   producto, merma ni cambio de precio se guarda automáticamente. El asistente
   propone y la app solo escribe en la base de datos cuando el usuario presiona
   "Confirmar". Un error del modelo nunca llega solo al inventario.

## Supuestos de comisiones

| Medio de cobro | Tasa usada | Fuente |
| --- | --- | --- |
| CoDi / DiMo / SPEI | 0 % | Banxico; Ley de Economía Digital |
| Efectivo | 0 % | Sin comisión financiera |
| Tarjeta en terminal (TPV) | 5 % | Rango 3.5 %–5.39 % para mipymes (conferencia de la ley) |

Todos los cálculos se presentan como estimaciones operativas, no como
determinaciones fiscales oficiales.

## Referencias de México usadas en el prompt

| Regla | Uso en el asistente | Fuente |
| --- | --- | --- |
| Precio exhibido = total a pagar; prohibido el recargo por tarjeta | Nunca sugiere cobrar más a quien paga con tarjeta | Ley Federal de Protección al Consumidor, art. 7 Bis; Profeco |
| Margen promedio de tienditas 20–25 % (casi 30 % antes de la pandemia) | Compara el margen con tarjeta y sugiere el precio para llegar a 25 % | ANPEC |
| 79 % de las tienditas redujo su margen para conservar clientes | Contexto al hablar de precios frente a cadenas | ANPEC, encuesta 28 |
| Productos gancho: refresco, cigarros, botanas, pan, leche, cerveza | Margen menor aceptable; resurtir es urgente | ANPEC / La Jornada (2011) |

## Acciones con tarjeta editable

El modelo nunca escribe en la base. Devuelve un JSON validado con Instructor +
Pydantic (`esquemas.py`); si es una acción, `tarjetas.py` arma una tarjeta
editable y la app solo guarda cuando el usuario presiona "Confirmar"
(`POST /confirmar` → `escritura.py`, en una transacción de Firestore).

| Acción | Campos obligatorios | Valores por defecto | Qué escribe |
| --- | --- | --- | --- |
| ENTRADA | producto, cantidad, costo por pieza, precio de venta | precio actual y último costo si el producto existe; proveedor "Proveedor General"; forma de pago "efectivo" | `entregas`, `detalle_entregas` y `productos` (existencia, costo promedio ponderado, precio); da de alta el producto si es nuevo |
| CAMBIO_PRECIO | producto existente, precio nuevo | — | `productos.precio_venta` |
| MERMA | producto existente, cantidad (no mayor a la existencia) | motivo "dañado" | resta existencia en `productos` y crea un documento en `mermas` |

Criterios:

- Lo que el usuario no dijo queda en `null`: el modelo no inventa. La tarjeta
  marca en naranja los campos que faltan y desactiva "Confirmar" hasta que se
  completan.
- El producto se resuelve en Python con los `alias` de Firestore, no con el
  modelo, así que no se inventan IDs.
- En una acción, el mensaje del chat lo arma Python ("Revisa la merma y
  presiona Confirmar"); el modelo no puede decir "registré" ni "guardé".
- Las ganancias, márgenes y pérdidas de la tarjeta se calculan en la app y en
  Python, no en el modelo.
- La app envía los últimos 6 mensajes como historial, para que "a 15 cada una"
  complete la entrada anterior.
