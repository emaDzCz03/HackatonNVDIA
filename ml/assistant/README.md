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
  la sección `[CONTEXTO]` (más adelante, desde la base de datos o RAG).

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
