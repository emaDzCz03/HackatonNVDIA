"""Pipeline completo en una VM de Colab (A100).

Se ejecuta desde la raíz del proyecto subido a /content/proyecto:
    ROBOFLOW_API_KEY=... python ml/vision/colab/pipeline.py [--n-sint 8000] [--horas 1.5]

Etapas (cada una se salta si ya terminó, así que se puede relanzar):
  1. Recortes de Open Food Facts con rembg (GPU).
  2. Datasets de Roboflow + galería de marcas.
  3. SKU-110K (en paralelo con la generación sintética).
  4. Generación sintética usando recortes rembg + recortes de Roboflow.
  5. Dataset mixto + entrenamiento YOLO11s.
El mejor peso queda en /content/datos/best.pt y el progreso en /content/datos/ESTADO.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROYECTO = Path(__file__).resolve().parents[3]
DATOS = Path("/content/datos")
PY = sys.executable


def estado(texto: str) -> None:
    print(f"\n=== {texto}", flush=True)
    (DATOS / "ESTADO").write_text(texto + "\n", encoding="utf-8")


def correr(*args: str, **kw) -> None:
    subprocess.run([PY, *args], check=True, cwd=PROYECTO, **kw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-sint", type=int, default=8000)
    parser.add_argument("--sku-max", type=int, default=4000)
    parser.add_argument("--modelo", default="yolo11s.pt")
    parser.add_argument("--imgsz", type=int, default=832)
    parser.add_argument("--epocas", type=int, default=60)
    parser.add_argument("--horas", type=float, default=1.5, help="Tope de tiempo de entrenamiento.")
    args = parser.parse_args()
    DATOS.mkdir(parents=True, exist_ok=True)

    recortes_off = DATOS / "recortes_off"
    if not any(recortes_off.glob("*.png")):
        estado("1/5 recortes rembg")
        correr("ml/vision/sintetico/recortes.py", "ml/vision/recolectadas/openfoodfacts",
               "--salida", str(recortes_off), "--rembg")

    if not (DATOS / "galeria").exists():
        estado("2/5 roboflow")
        correr("ml/vision/datos_internet.py", "roboflow", "--dir", str(DATOS))

    sku = None
    if not (DATOS / "SKU-110K" / "train.txt").exists():
        estado("3/5 SKU-110K en segundo plano")
        sku = subprocess.Popen([PY, "ml/vision/datos_internet.py", "sku", "--dir", str(DATOS)], cwd=PROYECTO,
                               stdout=open(DATOS / "sku.log", "w"), stderr=subprocess.STDOUT)

    sint = DATOS / "sintetico"
    hechas = len(list((sint / "images").glob("*.jpg"))) if sint.exists() else 0
    if hechas < args.n_sint:
        estado(f"4/5 sintético ({hechas}/{args.n_sint})")
        correr("ml/vision/sintetico/generar.py", "--recortes", str(recortes_off), str(DATOS / "galeria"),
               "--fondos", "ml/vision/recolectadas/commons", "--salida", str(sint),
               "--n", str(args.n_sint - hechas), "--inicio", str(hechas), "--procesos", str(os.cpu_count() or 4))

    if sku is not None:
        estado("3/5 esperando SKU-110K")
        if sku.wait() != 0:
            print("SKU-110K falló; se entrena sin él. Ver /content/datos/sku.log", flush=True)

    estado("5/5 armando dataset")
    correr("ml/vision/datos_internet.py", "armar", "--dir", str(DATOS), "--sku-max", str(args.sku_max))

    estado("5/5 entrenando")
    from ultralytics import YOLO

    modelo = YOLO(args.modelo)
    modelo.train(
        data=str(DATOS / "data_mixto.yaml"),
        epochs=args.epocas,
        time=args.horas,
        imgsz=args.imgsz,
        batch=32,
        workers=8,
        cache=False,
        close_mosaic=5,
        patience=15,
        project=str(DATOS / "runs"),
        name="anaquel_mx",
        exist_ok=True,
        plots=True,
    )
    mejor = DATOS / "runs" / "anaquel_mx" / "weights" / "best.pt"
    shutil.copy2(mejor, DATOS / "best.pt")
    metricas = YOLO(str(mejor)).val(data=str(DATOS / "data_mixto.yaml"), imgsz=args.imgsz, split="val")
    resumen = {"mAP50": round(float(metricas.box.map50), 4), "mAP50-95": round(float(metricas.box.map), 4)}
    for clase, ap in zip(metricas.box.ap_class_index, metricas.box.ap50):
        resumen[f"AP50_{metricas.names[int(clase)]}"] = round(float(ap), 4)
    print(resumen, flush=True)
    estado(f"LISTO {resumen}")


if __name__ == "__main__":
    main()
