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

## Dataset desde internet + sintético (Colab A100)

Sin fotos propias: el modelo nuevo usa `producto` en la clase 0 (antes
`bottle`), así que `api_auditoria.py` funciona sin cambios.

| Fuente | Qué aporta | Licencia |
| --- | --- | --- |
| `sintetico/generar.py` | Anaqueles generados con recortes de productos mexicanos; etiquetas exactas de `producto`, `empty_space` y `fallen_product` | Derivado de Open Food Facts (CC-BY-SA) y Roboflow |
| Roboflow Universe (`datos_internet.py roboflow`) | Fotos reales de OXXO/tiendita (Coca-Cola, Gansito, Chokis, Takis…) y góndolas densas | CC BY 4.0 / dominio público / MIT |
| SKU-110K (`datos_internet.py sku`) | 11 mil anaqueles densos para la clase `producto` | Uso académico |
| `tools/recolectar_imagenes.py` | Fotos de Open Food Facts México y Wikimedia Commons | CC-BY-SA / CC |

Los recortes de los datasets mexicanos con marca quedan en `galeria/<marca>/`
para identificar el producto por similitud.

En una VM de Colab (desde la raíz del proyecto en `/content/proyecto`):

```bash
pip install ultralytics roboflow "rembg[gpu]" onnxruntime-gpu
ROBOFLOW_API_KEY=... python ml/vision/colab/pipeline.py --n-sint 8000 --horas 1.5
```

El peso final queda en `/content/datos/best.pt`; cópialo a `models/best.pt`.
Para servir la API desde Colab con un túnel: `python ml/vision/colab/servir_api.py`.
