import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISION_DIR = PROJECT_ROOT / "ml" / "vision"
DATASET_DIR = VISION_DIR / "dataset"
INITIAL_WEIGHTS = PROJECT_ROOT / "models" / "best.pt"
TRAINED_WEIGHTS = VISION_DIR / "runs" / "retail_audit" / "weights" / "best.pt"
DESTINATION_WEIGHTS = PROJECT_ROOT / "models" / "best.pt"


def main() -> None:
    from ultralytics import YOLO, settings

    required_splits = ("train", "val", "test")
    missing_splits = [
        split for split in required_splits
        if not (DATASET_DIR / "images" / split).is_dir()
        or not (DATASET_DIR / "labels" / split).is_dir()
    ]
    if missing_splits:
        raise FileNotFoundError(
            "Faltan splits del dataset: "
            f"{', '.join(missing_splits)}. Ejecuta primero "
            "`python ml/vision/preparar_dataset.py`."
        )

    settings.update({"datasets_dir": str(VISION_DIR)})
    model = YOLO(str(INITIAL_WEIGHTS) if INITIAL_WEIGHTS.is_file() else "yolov8n.pt")
    model.train(
        data="ml/vision/data.yaml",
        epochs=50,
        imgsz=640,
        batch=16,
        workers=0,
        cache=True,
        project="ml/vision/runs",
        name="retail_audit",
        exist_ok=True,
    )

    if not TRAINED_WEIGHTS.is_file():
        raise FileNotFoundError(
            f"El entrenamiento terminó sin generar el peso esperado: {TRAINED_WEIGHTS}"
        )
    DESTINATION_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TRAINED_WEIGHTS, DESTINATION_WEIGHTS)
    print(f"Modelo guardado en {DESTINATION_WEIGHTS}")


if __name__ == "__main__":
    main()
