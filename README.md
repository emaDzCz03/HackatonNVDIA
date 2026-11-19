# App de recepción de mercancía

Aplicación web para registrar entregas de mercancía mediante foto, revisión manual,
asistente conversacional y cálculos deterministas de margen e inventario.

## Alcance inicial

- Catálogo congelado de 10 a 15 productos comunes.
- Recepción de mercancía y actualización de existencias.
- Costo de compra, precio de venta y margen por pieza.
- Registro de forma de pago.
- Sin ventas ni control contable completo en esta fase.

## Tecnologías previstas

- Frontend: React con PWA.
- Base de datos y autenticación: Firebase.
- Almacenamiento de imágenes: Firebase Storage.
- Servicios auxiliares: Firebase Functions.
- Visión: modelo base permisivo, por definir tras revisar licencias disponibles.
- Asistente: modelo pequeño permisivo con ajuste LoRA/QLoRA.

## Estructura

- `frontend/`: interfaz web y PWA.
- `firebase/`: reglas, índices y Functions.
- `ml/vision/`: datos, entrenamiento y evaluación de visión.
- `ml/assistant/`: datos sintéticos, evaluación y adaptadores del asistente.
- `docs/`: arquitectura, datos, evaluación y casos de prueba.
- `tests/`: pruebas automatizadas.

## Separación obligatoria de datos

Las fotos de visión deben dividirse por escena o sesión. No dividir archivos casi
idénticos al azar, porque produciría fuga de datos entre entrenamiento y prueba.

Los ejemplos del asistente deben identificarse explícitamente como sintéticos.
