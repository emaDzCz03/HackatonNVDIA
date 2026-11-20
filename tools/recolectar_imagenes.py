"""Recolecta imágenes con licencia libre de productos y anaqueles mexicanos.

Fuentes:
  * Wikimedia Commons: fotos de tienditas, abarrotes, anaqueles y refrescos
    en México (licencias CC / dominio público; se guarda la atribución).
  * Open Food Facts (México): fotos frontales de productos (licencia CC-BY-SA).

Salida en ml/vision/recolectadas/<fuente>/ con un metadatos.csv por fuente.
Con --prelabel usa models/best.pt para generar etiquetas YOLO preliminares y
copia a ml/vision/raw/ las imágenes donde el modelo detectó algo. Esas
etiquetas son un punto de partida: revísalas en CVAT/LabelImg antes de entrenar.

Uso (desde la raíz del proyecto):
    python tools/recolectar_imagenes.py                  # descarga todo
    python tools/recolectar_imagenes.py --prelabel       # descarga y pre-etiqueta
    python tools/recolectar_imagenes.py --solo commons --max-por-busqueda 30
"""

import argparse
import csv
import hashlib
import io
import re
import shutil
import sys
import time
from pathlib import Path

import requests
from PIL import Image

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "ml" / "vision" / "recolectadas"
RAW_IMAGES = RAIZ / "ml" / "vision" / "raw" / "images"
RAW_LABELS = RAIZ / "ml" / "vision" / "raw" / "labels"
MODELO = RAIZ / "models" / "best.pt"

SESION = requests.Session()
SESION.headers["User-Agent"] = "retail-shelf-dataset-builder/1.0 (educational)"

BUSQUEDAS_COMMONS = [
    "tienda de abarrotes México",
    "abarrotes interior Mexico",
    "tiendita Mexico shelves",
    "Mexican grocery store shelves",
    "supermercado México anaquel",
    "Oxxo interior",
    "refrescos México tienda",
    "Jarritos bottles",
    "Coca-Cola Mexico bottle",
    "Mexican soda bottles",
    "Sabritas",
    "Bimbo pan México",
    "cerveza mexicana botellas",
    "mercado México dulces tienda",
    "store shelves soft drinks",
    "empty supermarket shelves",
]

CATEGORIAS_OFF = [
    "en:sodas",
    "en:waters",
    "en:beers",
    "en:juices",
    "en:chips-and-fries",
    "en:breads",
    "en:milks",
    "en:biscuits",
    "en:candies",
    "en:instant-noodles",
]

LADO_MAX = 1280
LADO_MIN = 320


def _guardar(contenido: bytes, destino: Path) -> tuple[int, int] | None:
    """Valida, reduce a LADO_MAX y guarda como JPEG. Devuelve (ancho, alto)."""
    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except Exception:
        return None
    if min(imagen.size) < LADO_MIN:
        return None
    imagen = imagen.convert("RGB")
    imagen.thumbnail((LADO_MAX, LADO_MAX))
    destino.parent.mkdir(parents=True, exist_ok=True)
    imagen.save(destino, "JPEG", quality=90)
    return imagen.size


def _descargar(url: str, intentos: int = 3) -> bytes | None:
    for intento in range(intentos):
        try:
            r = SESION.get(url, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (intento + 1))
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            time.sleep(2)
    return None


def _limpiar_html(texto: str) -> str:
    return re.sub(r"<[^>]+>", "", texto or "").strip()


def recolectar_commons(max_por_busqueda: int, vistos: set[str]) -> int:
    carpeta = SALIDA / "commons"
    metadatos = carpeta / "metadatos.csv"
    existentes = {p.stem for p in carpeta.glob("*.jpg")}
    filas = []
    for busqueda in BUSQUEDAS_COMMONS:
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrnamespace": 6, "gsrsearch": f"{busqueda} filetype:bitmap",
            "gsrlimit": max_por_busqueda, "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": LADO_MAX,
        }
        try:
            datos = SESION.get("https://commons.wikimedia.org/w/api.php", params=params, timeout=30).json()
        except (requests.RequestException, ValueError) as error:
            print(f"  [commons] '{busqueda}': {error}")
            continue
        paginas = datos.get("query", {}).get("pages", {}).values()
        nuevas = 0
        for pagina in paginas:
            info = (pagina.get("imageinfo") or [{}])[0]
            if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            nombre = f"wmc{pagina['pageid']}__0"
            if nombre in existentes:
                continue
            contenido = _descargar(info.get("thumburl") or info["url"])
            if not contenido:
                continue
            huella = hashlib.md5(contenido).hexdigest()
            if huella in vistos:
                continue
            tam = _guardar(contenido, carpeta / f"{nombre}.jpg")
            if not tam:
                continue
            vistos.add(huella)
            existentes.add(nombre)
            meta = info.get("extmetadata", {})
            filas.append({
                "archivo": f"{nombre}.jpg",
                "titulo": pagina["title"],
                "busqueda": busqueda,
                "autor": _limpiar_html(meta.get("Artist", {}).get("value", "")),
                "licencia": meta.get("LicenseShortName", {}).get("value", ""),
                "url_licencia": meta.get("LicenseUrl", {}).get("value", ""),
                "fuente": info.get("descriptionurl", ""),
                "ancho": tam[0], "alto": tam[1],
            })
            nuevas += 1
            time.sleep(0.2)
        print(f"  [commons] '{busqueda}': {nuevas} nuevas")
    _anexar_csv(metadatos, filas)
    return len(filas)


def recolectar_off(max_por_categoria: int, vistos: set[str]) -> int:
    carpeta = SALIDA / "openfoodfacts"
    metadatos = carpeta / "metadatos.csv"
    existentes = {p.stem for p in carpeta.glob("*.jpg")}
    filas = []
    for categoria in CATEGORIAS_OFF:
        params = {
            "q": f'countries_tags:"en:mexico" AND categories_tags:"{categoria}"',
            "fields": "code,product_name,brands,image_front_url",
            "page_size": max_por_categoria,
        }
        try:
            hits = SESION.get("https://search.openfoodfacts.org/search", params=params, timeout=30).json().get("hits", [])
        except (requests.RequestException, ValueError) as error:
            print(f"  [off] {categoria}: {error}")
            continue
        nuevas = 0
        for producto in hits:
            url = producto.get("image_front_url")
            if not url:
                continue
            # Versión grande en lugar de la miniatura de 400 px.
            url = re.sub(r"\.(\d+)\.jpg$", ".full.jpg", url)
            nombre = f"off{producto['code']}__0"
            if nombre in existentes:
                continue
            contenido = _descargar(url)
            if not contenido:
                continue
            huella = hashlib.md5(contenido).hexdigest()
            if huella in vistos:
                continue
            tam = _guardar(contenido, carpeta / f"{nombre}.jpg")
            if not tam:
                continue
            vistos.add(huella)
            existentes.add(nombre)
            marcas = producto.get("brands") or []
            filas.append({
                "archivo": f"{nombre}.jpg",
                "codigo": producto["code"],
                "producto": producto.get("product_name", ""),
                "marca": ", ".join(marcas) if isinstance(marcas, list) else marcas,
                "categoria": categoria,
                "licencia": "CC-BY-SA 3.0",
                "fuente": f"https://mx.openfoodfacts.org/producto/{producto['code']}",
                "ancho": tam[0], "alto": tam[1],
            })
            nuevas += 1
            time.sleep(0.3)
        print(f"  [off] {categoria}: {nuevas} nuevas")
    _anexar_csv(metadatos, filas)
    return len(filas)


def _anexar_csv(ruta: Path, filas: list[dict]) -> None:
    if not filas:
        return
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not ruta.exists()
    with ruta.open("a", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
        if nuevo:
            escritor.writeheader()
        escritor.writerows(filas)


def pre_etiquetar(confianza: float) -> None:
    from ultralytics import YOLO

    modelo = YOLO(str(MODELO))
    imagenes = sorted(SALIDA.glob("*/*.jpg"))
    copiadas = 0
    for ruta in imagenes:
        resultado = modelo.predict(str(ruta), conf=confianza, verbose=False)[0]
        if len(resultado.boxes) == 0:
            continue
        lineas = [
            f"{int(c)} {x:.6f} {y:.6f} {w:.6f} {h:.6f}"
            for c, (x, y, w, h) in zip(resultado.boxes.cls.tolist(), resultado.boxes.xywhn.tolist())
        ]
        destino = RAW_IMAGES / ruta.parent.name / ruta.name
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ruta, destino)
        etiqueta = RAW_LABELS / ruta.parent.name / f"{ruta.stem}.txt"
        etiqueta.parent.mkdir(parents=True, exist_ok=True)
        etiqueta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        copiadas += 1
    print(f"Pre-etiquetadas {copiadas}/{len(imagenes)} imágenes en {RAW_IMAGES.relative_to(RAIZ)} (revisar antes de entrenar).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solo", choices=["commons", "off"], help="Usar una sola fuente.")
    parser.add_argument("--max-por-busqueda", type=int, default=40)
    parser.add_argument("--prelabel", action="store_true", help="Pre-etiquetar con models/best.pt.")
    parser.add_argument("--solo-prelabel", action="store_true", help="No descargar; solo pre-etiquetar.")
    parser.add_argument("--conf", type=float, default=0.35)
    args = parser.parse_args()

    if not args.solo_prelabel:
        vistos = {hashlib.md5(p.read_bytes()).hexdigest() for p in SALIDA.glob("*/*.jpg")}
        total = 0
        if args.solo in (None, "commons"):
            print("Wikimedia Commons…")
            total += recolectar_commons(args.max_por_busqueda, vistos)
        if args.solo in (None, "off"):
            print("Open Food Facts México…")
            total += recolectar_off(args.max_por_busqueda, vistos)
        print(f"Descargadas {total} imágenes nuevas en {SALIDA.relative_to(RAIZ)}")

    if args.prelabel or args.solo_prelabel:
        pre_etiquetar(args.conf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
