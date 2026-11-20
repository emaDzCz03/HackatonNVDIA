"""Convierte fotos de productos en recortes PNG con fondo transparente.

Usa rembg (U^2-Net) si está instalado; si no, quita fondos claros y uniformes
con relleno desde los bordes. Las fotos donde no se logra separar el producto
se descartan.

    python ml/vision/sintetico/recortes.py ENTRADA [ENTRADA ...] --salida DIR [--rembg]
"""

import argparse
from pathlib import Path

import cv2
import numpy as np

EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _quitar_fondo_uniforme(bgr: np.ndarray) -> np.ndarray | None:
    h, w = bgr.shape[:2]
    borde = np.concatenate([bgr[0], bgr[-1], bgr[:, 0], bgr[:, -1]])
    if borde.std(axis=0).mean() > 28:  # fondo con textura: no se puede separar así
        return None
    mascara = np.zeros((h + 2, w + 2), np.uint8)
    relleno = bgr.copy()
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if mascara[y + 1, x + 1] == 0:
            cv2.floodFill(relleno, mascara, (x, y), 0, (18, 18, 18), (18, 18, 18), flags)
    alfa = 255 - mascara[1:-1, 1:-1]
    alfa = cv2.morphologyEx(alfa, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return alfa


def recortar(bgr: np.ndarray, sesion=None) -> np.ndarray | None:
    if sesion is not None:
        from rembg import remove

        rgba = remove(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), session=sesion)
        alfa = np.asarray(rgba)[:, :, 3]
        alfa = np.where(alfa > 127, 255, 0).astype(np.uint8)
    else:
        alfa = _quitar_fondo_uniforme(bgr)
        if alfa is None:
            return None

    # Nos quedamos con el componente más grande (el producto).
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(alfa)
    if n <= 1:
        return None
    mayor = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    alfa = np.where(etiquetas == mayor, 255, 0).astype(np.uint8)
    x, y, w, h, area = stats[mayor]
    fraccion = area / alfa.size
    if not 0.12 <= fraccion <= 0.97 or min(w, h) < 80:
        return None
    alfa = cv2.GaussianBlur(alfa, (3, 3), 0)
    rgba = np.dstack([bgr, alfa])[y:y + h, x:x + w]
    return rgba


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("entradas", nargs="+", type=Path)
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--rembg", action="store_true")
    args = parser.parse_args()

    sesion = None
    if args.rembg:
        from rembg import new_session

        sesion = new_session("isnet-general-use")

    args.salida.mkdir(parents=True, exist_ok=True)
    fotos = [p for d in args.entradas for p in sorted(d.rglob("*")) if p.suffix.lower() in EXT]
    ok = 0
    for ruta in fotos:
        bgr = cv2.imread(str(ruta))
        if bgr is None:
            continue
        rgba = recortar(bgr, sesion)
        if rgba is None:
            continue
        cv2.imwrite(str(args.salida / f"{ruta.stem}.png"), rgba)
        ok += 1
    print(f"Recortes: {ok}/{len(fotos)} en {args.salida}")


if __name__ == "__main__":
    main()
