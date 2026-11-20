"""Generador de anaqueles sintéticos con etiquetas YOLO exactas.

Pega recortes de productos (PNG con transparencia) en repisas dibujadas al azar,
deja huecos (empty_space) y tira productos (fallen_product). Después aplica
perspectiva, iluminación, desenfoque, ruido y compresión JPEG.

Clases (las mismas IDs que usa api_auditoria.py):
    0 producto, 1 empty_space, 2 damaged_product (no se genera), 3 fallen_product

    python ml/vision/sintetico/generar.py --recortes DIR [DIR ...] --salida DIR --n 5000
        [--fondos DIR] [--semilla 0] [--procesos 8]
"""

import argparse
import random
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

PRODUCTO, VACIO, CAIDO = 0, 1, 3
TAMANOS = [(1024, 768), (768, 1024), (1024, 576), (960, 960)]

_RECORTES: list[np.ndarray] = []
_FONDOS: list[np.ndarray] = []


def _cargar(dirs: list[Path], con_alfa: bool, max_lado: int) -> list[np.ndarray]:
    imagenes = []
    for d in dirs:
        for ruta in sorted(d.rglob("*")):
            if ruta.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            img = cv2.imread(str(ruta), cv2.IMREAD_UNCHANGED if con_alfa else cv2.IMREAD_COLOR)
            if img is None:
                continue
            if con_alfa and (img.ndim != 3 or img.shape[2] != 4):
                img = cv2.cvtColor(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2BGRA)
            escala = max_lado / max(img.shape[:2])
            if escala < 1:
                img = cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
            imagenes.append(img)
    return imagenes


def _iniciar(recortes: list[Path], fondos: list[Path]) -> None:
    global _RECORTES, _FONDOS
    _RECORTES = _cargar(recortes, True, 400)
    _FONDOS = _cargar(fondos, False, 1024) if fondos else []


# ----------------------------------------------------------------- dibujo base
def _pared(w: int, h: int, rng: random.Random) -> np.ndarray:
    if _FONDOS and rng.random() < 0.6:
        f = rng.choice(_FONDOS)
        f = cv2.resize(f, (w, h))
        f = cv2.GaussianBlur(f, (0, 0), rng.uniform(8, 25))
        return np.clip(f * rng.uniform(0.45, 0.9), 0, 255).astype(np.uint8)
    # Colores típicos de tiendita: blanco, crema, verde agua, azul, rosa mexicano, gris.
    base = rng.choice([(235, 235, 235), (200, 225, 240), (190, 215, 170), (220, 180, 120),
                       (170, 120, 230), (150, 150, 150), (60, 60, 60), (120, 170, 220)])
    pared = np.empty((h, w, 3), np.float32)
    pared[:] = base
    gradiente = np.linspace(rng.uniform(0.6, 1.0), rng.uniform(0.6, 1.0), h)[:, None, None]
    pared *= gradiente
    pared += np.random.default_rng(rng.randrange(1 << 30)).normal(0, 6, pared.shape)
    return np.clip(pared, 0, 255).astype(np.uint8)


def _repisa(img: np.ndarray, y: int, grosor: int, rng: random.Random) -> None:
    w = img.shape[1]
    color = rng.choice([(230, 230, 230), (190, 190, 195), (40, 40, 40), (60, 100, 150),
                        (30, 120, 200), (200, 200, 60), (20, 20, 160)])
    cv2.rectangle(img, (0, y), (w, y + grosor), color, -1)
    cv2.line(img, (0, y), (w, y), tuple(min(255, c + 40) for c in color), max(1, grosor // 6))
    # Etiquetas de precio en el frente de la repisa.
    if rng.random() < 0.7:
        x = rng.randint(0, 60)
        while x < w:
            ancho = rng.randint(40, 80)
            fondo = rng.choice([(255, 255, 255), (80, 230, 255), (60, 220, 120)])
            alto = int(grosor * 0.8)
            y0 = y + (grosor - alto) // 2
            cv2.rectangle(img, (x, y0), (x + ancho, y0 + alto), fondo, -1)
            if alto > 10:
                cv2.putText(img, f"${rng.randint(5, 99)}.{rng.choice(['00', '50', '90'])}", (x + 3, y0 + alto - 3),
                            cv2.FONT_HERSHEY_SIMPLEX, min(alto / 32, ancho / 110), (0, 0, 0), 1, cv2.LINE_AA)
            x += ancho + rng.randint(60, 220)


def _pegar(img: np.ndarray, rgba: np.ndarray, x: int, y: int) -> tuple[int, int, int, int] | None:
    """Pega rgba con esquina superior izquierda en (x, y). Devuelve la caja visible."""
    h, w = rgba.shape[:2]
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        return None
    parche = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    alfa = parche[:, :, 3:4].astype(np.float32) / 255
    zona = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (parche[:, :, :3] * alfa + zona * (1 - alfa)).astype(np.uint8)
    ys, xs = np.where(parche[:, :, 3] > 40)
    if len(xs) == 0:
        return None
    return x0 + xs.min(), y0 + ys.min(), x0 + xs.max(), y0 + ys.max()


def _variar_producto(rgba: np.ndarray, alto: int, rng: random.Random) -> np.ndarray:
    h, w = rgba.shape[:2]
    alto = max(8, int(alto * rng.uniform(0.94, 1.06)))
    ancho = max(4, int(w * alto / h))
    p = cv2.resize(rgba, (ancho, alto), interpolation=cv2.INTER_AREA)
    color = p[:, :, :3].astype(np.float32) * rng.uniform(0.7, 1.15) + rng.uniform(-15, 15)
    p[:, :, :3] = np.clip(color, 0, 255).astype(np.uint8)
    if rng.random() < 0.3:
        angulo = rng.uniform(-4, 4)
        M = cv2.getRotationMatrix2D((ancho / 2, alto / 2), angulo, 1)
        p = cv2.warpAffine(p, M, (ancho, alto), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    return p


# ----------------------------------------------------------------- una imagen
def generar_una(semilla: int) -> tuple[np.ndarray, list[tuple[int, float, float, float, float]]]:
    rng = random.Random(semilla)
    W, H = rng.choice(TAMANOS)
    img = _pared(W, H, rng)
    cajas: list[tuple[int, int, int, int, int]] = []

    filas = rng.choice([1, 2, 2, 3, 3, 3, 4, 4, 5])
    alto_fila = H / filas
    y_inicio = rng.uniform(-0.3, 0.1) * alto_fila

    for f in range(filas + 1):
        y_top = y_inicio + f * alto_fila
        grosor = max(6, int(alto_fila * rng.uniform(0.05, 0.1)))
        y_repisa = int(y_top + alto_fila - grosor)
        if y_repisa < -grosor or y_top > H:
            continue
        alto_prod = int(alto_fila * rng.uniform(0.5, 0.86))
        x = int(rng.uniform(-0.15, 0.03) * W)
        fila_vacia = rng.random() < 0.05

        while x < W:
            # Hueco: deja ver la pared; se etiqueta como empty_space.
            if fila_vacia or rng.random() < 0.13:
                ancho_ref = alto_prod * rng.uniform(0.35, 0.8)
                ancho_hueco = int(ancho_ref * rng.uniform(1, 3.5)) if not fila_vacia else W
                x0, x1 = x, x + ancho_hueco
                cajas.append((VACIO, x0, y_repisa - alto_prod, x1, y_repisa))
                if rng.random() < 0.3 and _RECORTES:  # producto caído dentro del hueco
                    p = _variar_producto(rng.choice(_RECORTES), int(alto_prod * rng.uniform(0.75, 1)), rng)
                    p = cv2.rotate(p, rng.choice([cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]))
                    px = x0 + rng.randint(0, max(0, ancho_hueco - p.shape[1]))
                    caja = _pegar(img, p, px, y_repisa - p.shape[0])
                    if caja:
                        cajas.append((CAIDO, *caja))
                x = x1 + rng.randint(0, 8)
                if fila_vacia:
                    break
                continue

            # Bloque de frentes del mismo producto, como en un anaquel real.
            recorte = rng.choice(_RECORTES)
            for _ in range(rng.choice([1, 2, 2, 3, 3, 4, 5, 6])):
                if x >= W:
                    break
                p = _variar_producto(recorte, alto_prod, rng)
                y = y_repisa - p.shape[0] + rng.randint(0, 2)
                caja = _pegar(img, p, x, y)
                if caja:
                    cajas.append((PRODUCTO, *caja))
                x += p.shape[1] + int(p.shape[1] * rng.uniform(-0.06, 0.07))

        # Producto caído encima de la fila (acostado delante de los demás).
        if rng.random() < 0.08 and _RECORTES:
            p = _variar_producto(rng.choice(_RECORTES), int(alto_prod * 0.8), rng)
            M = cv2.getRotationMatrix2D((p.shape[1] / 2, p.shape[0] / 2), rng.uniform(60, 120), 1)
            lado = int(np.hypot(*p.shape[:2]))
            M[:, 2] += [(lado - p.shape[1]) / 2, (lado - p.shape[0]) / 2]
            p = cv2.warpAffine(p, M, (lado, lado), borderValue=(0, 0, 0, 0))
            caja = _pegar(img, p, rng.randint(0, max(1, W - lado)), y_repisa - int(lado * 0.8))
            if caja:
                cajas.append((CAIDO, *caja))

        _repisa(img, y_repisa, grosor, rng)

    img, cajas = _perspectiva(img, cajas, rng)
    img = _fotometria(img, rng)
    return img, _a_yolo(cajas, img.shape[1], img.shape[0])


# ----------------------------------------------------------------- aumentos
def _perspectiva(img, cajas, rng):
    H, W = img.shape[:2]
    d = 0.06
    origen = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    destino = origen + np.float32([[rng.uniform(-d, d) * W, rng.uniform(-d, d) * H] for _ in range(4)])
    M = cv2.getPerspectiveTransform(origen, destino)
    img = cv2.warpPerspective(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    nuevas = []
    for c, x0, y0, x1, y1 in cajas:
        pts = cv2.perspectiveTransform(np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]), M)[0]
        nuevas.append((c, pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()))
    return img, nuevas


def _fotometria(img, rng):
    img = img.astype(np.float32)
    img = img * rng.uniform(0.6, 1.25) + rng.uniform(-25, 25)
    img *= np.float32([rng.uniform(0.88, 1.12) for _ in range(3)])  # temperatura de color
    if rng.random() < 0.5:  # sombra / luz desigual
        H, W = img.shape[:2]
        gx = np.linspace(rng.uniform(0.55, 1), rng.uniform(0.55, 1), W)[None, :, None]
        gy = np.linspace(rng.uniform(0.7, 1), rng.uniform(0.7, 1), H)[:, None, None]
        img *= gx * gy
    img = np.clip(img, 0, 255).astype(np.uint8)
    if rng.random() < 0.3:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.6, 1.8))
    if rng.random() < 0.15:  # movimiento de la mano
        k = rng.choice([5, 7, 9])
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2] = 1 / k
        if rng.random() < 0.5:
            kernel = kernel.T
        img = cv2.filter2D(img, -1, kernel)
    if rng.random() < 0.5:
        ruido = np.random.default_rng(rng.randrange(1 << 30)).normal(0, rng.uniform(2, 9), img.shape)
        img = np.clip(img + ruido, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(55, 95)])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _a_yolo(cajas, W, H):
    salida = []
    for c, x0, y0, x1, y1 in cajas:
        area = max(1e-6, (x1 - x0) * (y1 - y0))
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if cx1 - cx0 < 6 or cy1 - cy0 < 6:
            continue
        if (cx1 - cx0) * (cy1 - cy0) / area < 0.4:  # casi fuera de cuadro
            continue
        salida.append((c, (cx0 + cx1) / 2 / W, (cy0 + cy1) / 2 / H, (cx1 - cx0) / W, (cy1 - cy0) / H))
    return salida


# ----------------------------------------------------------------- lote
def _trabajo(args):
    indice, semilla, salida = args
    img, etiquetas = generar_una(semilla)
    nombre = f"sint{indice:06d}__0"
    cv2.imwrite(str(salida / "images" / f"{nombre}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    (salida / "labels" / f"{nombre}.txt").write_text(
        "".join(f"{c} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n" for c, x, y, w, h in etiquetas), encoding="utf-8")
    return len(etiquetas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recortes", nargs="+", type=Path, required=True)
    parser.add_argument("--fondos", nargs="*", type=Path, default=[])
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--inicio", type=int, default=0, help="Índice inicial (para lotes en bucle).")
    parser.add_argument("--semilla", type=int, default=0)
    parser.add_argument("--procesos", type=int, default=4)
    args = parser.parse_args()

    (args.salida / "images").mkdir(parents=True, exist_ok=True)
    (args.salida / "labels").mkdir(parents=True, exist_ok=True)
    tareas = [(i, args.semilla * 1_000_003 + i, args.salida) for i in range(args.inicio, args.inicio + args.n)]
    with Pool(args.procesos, initializer=_iniciar, initargs=(args.recortes, args.fondos)) as pool:
        total = sum(pool.imap_unordered(_trabajo, tareas, chunksize=8))
    print(f"Generadas {args.n} imágenes con {total} cajas en {args.salida}")


if __name__ == "__main__":
    main()
