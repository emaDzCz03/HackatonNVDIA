import argparse
import random
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path


VISION_DIR = Path(__file__).resolve().parent
RAW_IMAGES_DIR = VISION_DIR / "raw" / "images"
RAW_LABELS_DIR = VISION_DIR / "raw" / "labels"
DATASET_DIR = VISION_DIR / "dataset"
CLASSES = {
    0: "bottle",
    1: "empty_space",
    2: "damaged_product",
    3: "fallen_product",
}
IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
SPLIT_RATIOS = {"train": 0.7, "val": 0.2, "test": 0.1}


def scene_group(relative_path: Path) -> str:
    stem = relative_path.stem
    if "__" in stem:
        prefix = stem.split("__", maxsplit=1)[0]
    else:
        prefix = re.sub(r"[_-]\d+$", "", stem) or stem
    return f"{relative_path.parent.as_posix()}::{prefix}"


def validate_label(label_path: Path, class_counts: dict[int, int]) -> list[str]:
    errors = []
    try:
        lines = label_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return [f"{label_path}: no se pudo leer: {error}"]

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            errors.append(
                f"{label_path}:{line_number}: se esperaban 5 valores "
                "(class_id x_center y_center width height)."
            )
            continue

        try:
            class_id = int(fields[0])
            coordinates = [float(value) for value in fields[1:]]
        except ValueError:
            errors.append(f"{label_path}:{line_number}: contiene valores no numéricos.")
            continue

        if class_id not in CLASSES:
            errors.append(
                f"{label_path}:{line_number}: class_id {class_id} fuera del rango 0–3."
            )
            continue
        if any(not 0 <= value <= 1 for value in coordinates):
            errors.append(
                f"{label_path}:{line_number}: las coordenadas deben estar normalizadas entre 0 y 1."
            )
            continue
        if coordinates[2] == 0 or coordinates[3] == 0:
            errors.append(f"{label_path}:{line_number}: width y height deben ser mayores que 0.")
            continue

        class_counts[class_id] += 1

    return errors


def assign_groups(
    groups: dict[str, list[Path]],
    seed: int,
) -> dict[str, list[Path]]:
    if len(groups) < len(SPLIT_RATIOS):
        raise ValueError(
            "Se necesitan al menos 3 grupos de escena/sesión distintos para crear "
            "train, val y test sin mezclar grupos."
        )

    shuffled_groups = list(groups.items())
    random.Random(seed).shuffle(shuffled_groups)
    shuffled_groups.sort(key=lambda item: len(item[1]), reverse=True)

    total_images = sum(len(paths) for paths in groups.values())
    targets = {
        split: total_images * ratio for split, ratio in SPLIT_RATIOS.items()
    }
    image_counts = dict.fromkeys(SPLIT_RATIOS, 0)
    group_counts = dict.fromkeys(SPLIT_RATIOS, 0)
    assignments = {split: [] for split in SPLIT_RATIOS}

    for index, (_, paths) in enumerate(shuffled_groups):
        remaining_groups = len(shuffled_groups) - index
        empty_splits = [split for split, count in group_counts.items() if count == 0]
        candidates = empty_splits if remaining_groups == len(empty_splits) else list(SPLIT_RATIOS)

        def score(candidate: str) -> float:
            return sum(
                (
                    (
                        image_counts[split] + (len(paths) if split == candidate else 0)
                        - targets[split]
                    )
                    / max(targets[split], 1)
                ) ** 2
                for split in SPLIT_RATIOS
            )

        destination = min(candidates, key=score)
        assignments[destination].extend(paths)
        image_counts[destination] += len(paths)
        group_counts[destination] += 1

    return assignments


def prepare_output_dirs(overwrite: bool) -> None:
    output_dirs = [
        DATASET_DIR / kind / split
        for kind in ("images", "labels")
        for split in SPLIT_RATIOS
    ]
    populated_dirs = [
        directory
        for directory in output_dirs
        if directory.exists() and any(directory.rglob("*"))
    ]
    if populated_dirs and not overwrite:
        raise FileExistsError(
            "El dataset de salida ya contiene archivos. Vuelve a ejecutar con "
            "--overwrite para reemplazar únicamente images/{train,val,test} y "
            "labels/{train,val,test} generados por este script."
        )

    if overwrite:
        for directory in output_dirs:
            if directory.is_symlink():
                raise ValueError(f"No se reemplaza una carpeta enlazada: {directory}")
            if directory.exists():
                shutil.rmtree(directory)

    for directory in output_dirs:
        directory.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida anotaciones YOLO y separa datos por escena/sesión."
    )
    parser.add_argument("--seed", type=int, default=42, help="Semilla para reproducir el split.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Reemplaza únicamente las seis carpetas de split generadas.",
    )
    args = parser.parse_args()

    if not RAW_IMAGES_DIR.is_dir() or not RAW_LABELS_DIR.is_dir():
        print(
            "Crea ml/vision/raw/images y ml/vision/raw/labels y coloca allí "
            "imágenes y etiquetas YOLO con rutas relativas iguales.",
            file=sys.stderr,
        )
        return 1

    images = sorted(
        path for path in RAW_IMAGES_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not images:
        print(f"No se encontraron imágenes en {RAW_IMAGES_DIR}.", file=sys.stderr)
        return 1

    image_pairs: list[tuple[Path, Path, Path]] = []
    errors = []
    class_counts = dict.fromkeys(CLASSES, 0)
    relative_labels = set()
    seen_stems = set()

    for image_path in images:
        relative_image = image_path.relative_to(RAW_IMAGES_DIR)
        label_path = (RAW_LABELS_DIR / relative_image).with_suffix(".txt")
        relative_label = label_path.relative_to(RAW_LABELS_DIR)
        if relative_image.with_suffix("").as_posix() in seen_stems:
            errors.append(f"{image_path}: hay otra imagen con el mismo nombre base.")
            continue
        seen_stems.add(relative_image.with_suffix("").as_posix())

        if not label_path.is_file():
            errors.append(f"{image_path}: falta su etiqueta {label_path}.")
            continue
        errors.extend(validate_label(label_path, class_counts))
        relative_labels.add(relative_label)
        image_pairs.append((image_path, label_path, relative_image))

    for label_path in RAW_LABELS_DIR.rglob("*.txt"):
        relative_label = label_path.relative_to(RAW_LABELS_DIR)
        if relative_label not in relative_labels:
            errors.append(f"{label_path}: no tiene una imagen correspondiente.")

    print("Instancias encontradas:")
    for class_id, class_name in CLASSES.items():
        print(f"  {class_id} {class_name}: {class_counts[class_id]}")
        if class_id in (2, 3) and class_counts[class_id] == 0:
            print(f"  ADVERTENCIA: no hay ejemplos de {class_name}.")

    if errors:
        print(f"\nSe encontraron {len(errors)} errores de etiquetas:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    grouped_images: dict[str, list[Path]] = defaultdict(list)
    pair_by_relative = {}
    for image_path, _, relative_image in image_pairs:
        grouped_images[scene_group(relative_image)].append(relative_image)
        pair_by_relative[relative_image] = image_path

    try:
        assignments = assign_groups(grouped_images, args.seed)
        prepare_output_dirs(args.overwrite)
    except (FileExistsError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    for split, relative_images in assignments.items():
        for relative_image in relative_images:
            source_image = pair_by_relative[relative_image]
            source_label = (RAW_LABELS_DIR / relative_image).with_suffix(".txt")
            destination_image = DATASET_DIR / "images" / split / relative_image
            destination_label = DATASET_DIR / "labels" / split / relative_image.with_suffix(".txt")
            destination_image.parent.mkdir(parents=True, exist_ok=True)
            destination_label.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_image, destination_image)
            shutil.copy2(source_label, destination_label)

    total = len(image_pairs)
    print(f"\nDataset preparado: {total} imágenes en {len(grouped_images)} grupos.")
    for split, paths in assignments.items():
        percent = len(paths) / total * 100
        print(f"  {split}: {len(paths)} imágenes ({percent:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
