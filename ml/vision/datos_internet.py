"""Descarga datasets públicos de anaqueles y arma el dataset mixto de entrenamiento.

Subcomandos:
    roboflow  Descarga los datasets de Roboflow Universe (requiere ROBOFLOW_API_KEY),
              guarda recortes por marca en galeria/ para la identificación.
    sku       Descarga y convierte SKU-110K (13.6 GB) con el script de Ultralytics.
    armar     Junta sintético + Roboflow + SKU-110K en dataset_mixto/ con las
              clases del proyecto y escribe data_mixto.yaml.

    python ml/vision/datos_internet.py roboflow --dir /content/datos
    python ml/vision/datos_internet.py sku --dir /content/datos
    python ml/vision/datos_internet.py armar --dir /content/datos --sku-max 4000
"""

import argparse
import os
import random
import re
import shutil
from pathlib import Path

import yaml

# (workspace, proyecto, versión, licencia). Todas las clases se convierten a "producto".
ROBOFLOW = [
    ("ipsum", "productosoxxo_v02", 3, "CC BY 4.0"),
    ("ipsum", "productosoxxo", 1, "Public Domain"),
    ("h3-consulting", "mexican-retail-product", 2, "CC BY 4.0"),
    ("ipsum", "tienditacharola", 3, "Public Domain"),
    ("ipsum", "tiendita2", 5, "CC BY 4.0"),
    ("minimarket", "productos-minimarket-augmented-2", 702, "MIT"),
    ("gondolize", "products_store", 1, "CC BY 4.0"),
]
# Datasets mexicanos con marca: sus recortes alimentan la galería de identificación.
CON_MARCA = {"productosoxxo_v02", "productosoxxo", "mexican-retail-product", "tienditacharola", "tiendita2"}

NOMBRES = {0: "producto", 1: "empty_space", 2: "damaged_product", 3: "fallen_product"}
SPLITS_RF = {"train": "train", "valid": "val", "test": "test"}


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-") or "x"


def cmd_roboflow(base: Path) -> None:
    import cv2
    from roboflow import Roboflow

    rf = Roboflow(api_key=os.environ["ROBOFLOW_API_KEY"])
    galeria = base / "galeria"
    for ws, proyecto, version, licencia in ROBOFLOW:
        destino = base / "roboflow" / proyecto
        if not (destino / "data.yaml").exists():
            print(f"Descargando {ws}/{proyecto} v{version} ({licencia})")
            rf.workspace(ws).project(proyecto).version(version).download("yolov8", location=str(destino), overwrite=True)
        nombres = yaml.safe_load((destino / "data.yaml").read_text(encoding="utf-8"))["names"]
        if isinstance(nombres, dict):
            nombres = [nombres[k] for k in sorted(nombres)]
        if proyecto not in CON_MARCA:
            continue
        # Recortes por marca para la galería (y como recortes extra del generador).
        for etiqueta in destino.glob("*/labels/*.txt"):
            imagen = next((etiqueta.parent.parent / "images").glob(etiqueta.stem + ".*"), None)
            if imagen is None:
                continue
            img = cv2.imread(str(imagen))
            if img is None:
                continue
            H, W = img.shape[:2]
            for i, linea in enumerate(etiqueta.read_text().splitlines()):
                partes = linea.split()
                if len(partes) != 5:
                    continue
                c, x, y, w, h = int(partes[0]), *map(float, partes[1:])
                x0, y0, x1, y1 = int((x - w / 2) * W), int((y - h / 2) * H), int((x + w / 2) * W), int((y + h / 2) * H)
                recorte = img[max(0, y0):y1, max(0, x0):x1]
                if recorte.size == 0 or min(recorte.shape[:2]) < 40 or c >= len(nombres):
                    continue
                marca = _slug(str(nombres[c]))
                if marca in {"x", "0"}:
                    continue
                carpeta = galeria / marca
                carpeta.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(carpeta / f"{proyecto}_{etiqueta.stem[:40]}_{i}.jpg"), recorte)
    marcas = sorted(p.name for p in galeria.iterdir()) if galeria.exists() else []
    print(f"Galería: {len(marcas)} marcas -> {marcas}")


def cmd_sku(base: Path) -> None:
    from ultralytics.data.utils import check_det_dataset
    from ultralytics.utils import SETTINGS

    SETTINGS.update({"datasets_dir": str(base)})
    info = check_det_dataset("SKU-110K.yaml", autodownload=True)
    print("SKU-110K listo en", info["path"])


def _enlazar(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() or destino.is_symlink():
        destino.unlink()
    try:
        os.symlink(origen.resolve(), destino)
    except OSError:
        shutil.copy2(origen, destino)


def _agregar(salida: Path, split: str, prefijo: str, imagen: Path, lineas: list[str]) -> None:
    nombre = f"{prefijo}__{imagen.stem}"[:150]
    _enlazar(imagen, salida / "images" / split / f"{nombre}{imagen.suffix.lower()}")
    (salida / "labels" / split).mkdir(parents=True, exist_ok=True)
    (salida / "labels" / split / f"{nombre}.txt").write_text("".join(l + "\n" for l in lineas), encoding="utf-8")


def _todo_producto(etiqueta: Path) -> list[str]:
    if not etiqueta.exists():
        return []
    lineas = []
    for l in etiqueta.read_text().splitlines():
        p = l.split()
        if len(p) == 5:  # solo cajas; polígonos se descartan
            lineas.append("0 " + " ".join(p[1:]))
    return lineas


def cmd_armar(base: Path, sku_max: int, sint_dir: Path | None, semilla: int) -> None:
    rng = random.Random(semilla)
    salida = base / "dataset_mixto"
    if salida.exists():
        shutil.rmtree(salida)
    conteo: dict[str, int] = {}

    def sumar(clave):
        conteo[clave] = conteo.get(clave, 0) + 1

    # 1) Sintético: 95 % train, 5 % val (la validación importante es la real).
    sint_dir = sint_dir or base / "sintetico"
    for img in sorted((sint_dir / "images").glob("*.jpg")):
        lineas = (sint_dir / "labels" / f"{img.stem}.txt").read_text().splitlines()
        split = "val" if rng.random() < 0.05 else "train"
        _agregar(salida, split, "sint", img, lineas)
        sumar(f"sintetico/{split}")

    # 2) Roboflow: se respetan sus splits; todas las clases -> producto.
    for _, proyecto, _, _ in ROBOFLOW:
        raiz = base / "roboflow" / proyecto
        for carpeta_rf, split in SPLITS_RF.items():
            for img in sorted((raiz / carpeta_rf / "images").glob("*")):
                lineas = _todo_producto(raiz / carpeta_rf / "labels" / f"{img.stem}.txt")
                _agregar(salida, split, f"rf-{proyecto}", img, lineas)
                sumar(f"roboflow/{split}")

    # 3) SKU-110K (submuestreado para no ahogar lo mexicano).
    sku = base / "SKU-110K"
    if sku.exists():
        for lista, split, maximo in (("train.txt", "train", sku_max), ("val.txt", "val", max(300, sku_max // 10))):
            rutas = [l.strip() for l in (sku / lista).read_text().splitlines() if l.strip()]
            rng.shuffle(rutas)
            for rel in rutas[:maximo]:
                img = (sku / rel).resolve()
                lineas = _todo_producto(sku / "labels" / f"{img.stem}.txt")
                _agregar(salida, split, "sku", img, lineas)
                sumar(f"sku/{split}")

    data = {"path": str(salida), "train": "images/train", "val": "images/val", "names": NOMBRES}
    if (salida / "images" / "test").exists():
        data["test"] = "images/test"
    (base / "data_mixto.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    for clave in sorted(conteo):
        print(f"  {clave}: {conteo[clave]}")
    print(f"Dataset mixto en {salida}; yaml en {base / 'data_mixto.yaml'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("comando", choices=["roboflow", "sku", "armar"])
    parser.add_argument("--dir", type=Path, default=Path("datos"))
    parser.add_argument("--sku-max", type=int, default=4000)
    parser.add_argument("--sintetico", type=Path)
    parser.add_argument("--semilla", type=int, default=0)
    args = parser.parse_args()
    args.dir.mkdir(parents=True, exist_ok=True)
    if args.comando == "roboflow":
        cmd_roboflow(args.dir)
    elif args.comando == "sku":
        cmd_sku(args.dir)
    else:
        cmd_armar(args.dir, args.sku_max, args.sintetico, args.semilla)


if __name__ == "__main__":
    main()
