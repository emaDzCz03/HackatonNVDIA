# Pipeline de visión para auditoría de anaqueles

El detector usa cuatro clases:

| ID | Clase |
| --- | --- |
| 0 | `bottle` |
| 1 | `empty_space` |
| 2 | `damaged_product` |
| 3 | `fallen_product` |

## Preparar y entrenar

1. Etiqueta las cajas de cada objeto en las imágenes usando CVAT, LabelImg,
   Roboflow u otra herramienta. Incluye ejemplos reales de las cuatro clases.
2. Exporta en formato YOLO Detection: una etiqueta `.txt` por imagen, con líneas
   `class_id x_center y_center width height` y coordenadas normalizadas entre
   0 y 1. Coloca las fotos en `ml/vision/raw/images/` y los `.txt` con la misma
   estructura relativa en `ml/vision/raw/labels/`. Las clases y rutas están
   definidas en `ml/vision/data.yaml`.
3. Desde la raíz del proyecto ejecuta:

   ```powershell
   python ml/vision/preparar_dataset.py
   ```

   El script valida las parejas imagen/etiqueta y los valores YOLO, reporta las
   instancias por clase y avisa si faltan ejemplos dañados o caídos. Separa por
   prefijo de escena/sesión: usa nombres como `sesion01__foto001.jpg` y
   `sesion01__foto001.txt`, o `sesion01_001.jpg`. El prefijo antes de `__` o el
   número final se mantiene en un solo split para evitar fuga entre escenas.
   Las proporciones 70/20/10 son aproximadas cuando un grupo contiene varias
   imágenes. Para reemplazar un dataset existente, usa
   `python ml/vision/preparar_dataset.py --overwrite`.
4. Instala las dependencias del proyecto y ejecuta desde la raíz:

   ```powershell
   python ml/vision/train.py
   ```

   El entrenamiento usa `models/best.pt` como punto de partida si existe; en
   caso contrario, usa `yolov8n.pt`. Entrena 50 épocas a tamaño 640 y copia el
   mejor checkpoint de `ml/vision/runs/retail_audit/weights/best.pt` a
   `models/best.pt`.

El ZIP disponible en `docs/datos` contiene 98 fotos y conteos agregados, no
etiquetas de cajas YOLO. No tiene ejemplos registrados como dañados o caídos;
esas clases requieren fotos anotadas para que el detector pueda aprenderlas.
