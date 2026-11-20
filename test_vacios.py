import argparse
import sys
from pathlib import Path
from zipfile import ZipFile

import cv2
import numpy as np
from ultralytics import YOLO


PROJECT_PATH = Path(__file__).resolve().parent
DEFAULT_IMAGES_PATH = PROJECT_PATH / "docs" / "datos"
MODEL_PATH = PROJECT_PATH / "models" / "best.pt"
MODEL_FALLBACK_PATH = PROJECT_PATH / "models" / "best_v3.pt"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
UMBRAL_POR_CLASE = {
    0: 0.35,
    1: 0.20,
    2: 0.30,
    3: 0.30,
}


def image_sources(images_path: Path):
    for path in sorted(images_path.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            yield str(path), path.read_bytes()
        elif path.suffix.lower() == ".zip":
            with ZipFile(path) as archive:
                for member in archive.infolist():
                    is_image = Path(member.filename).suffix.lower() in IMAGE_EXTENSIONS
                    if not member.is_dir() and is_image:
                        yield f"{path.name}/{member.filename}", archive.read(member)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cuenta en cuántas imágenes el modelo detecta espacios vacíos."
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=DEFAULT_IMAGES_PATH,
        help="Carpeta con imágenes (también procesa imágenes dentro de archivos ZIP).",
    )
    args = parser.parse_args()

    if not args.images_dir.is_dir():
        parser.error(f"No existe la carpeta de imágenes: {args.images_dir}")

    model_path = MODEL_PATH if MODEL_PATH.is_file() else MODEL_FALLBACK_PATH
    if not model_path.is_file():
        parser.error("No se encontró models/best.pt ni models/best_v3.pt.")
    if model_path == MODEL_FALLBACK_PATH:
        print(f"Usando el checkpoint disponible: {model_path}", file=sys.stderr)

    model = YOLO(str(model_path), task="detect")
    total_images = 0
    images_with_empty_space = 0

    for image_name, image_bytes in image_sources(args.images_dir):
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            print(f"Se omite una imagen no válida: {image_name}", file=sys.stderr)
            continue

        result = model.predict(
            source=image,
            conf=0.20,
            iou=0.45,
            imgsz=640,
            verbose=False,
        )[0]
        total_images += 1
        if result.boxes is None:
            continue

        class_ids = result.boxes.cls.cpu().numpy().astype(int)
        confidences = result.boxes.conf.cpu().numpy()
        if any(
            int(class_id) == 1
            and float(confidence) >= UMBRAL_POR_CLASE[int(class_id)]
            for class_id, confidence in zip(class_ids, confidences)
        ):
            images_with_empty_space += 1

    if total_images == 0:
        parser.error(f"No se encontraron imágenes compatibles en {args.images_dir}.")

    percentage = round(images_with_empty_space / total_images * 100, 1)
    print(
        f"Fotos con empty_space: {images_with_empty_space}/{total_images} "
        f"({percentage}%)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
