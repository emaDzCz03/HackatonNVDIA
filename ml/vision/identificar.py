"""Identifica la marca de cada producto detectado comparándolo con una galería.

No requiere entrenamiento: cada recorte se describe con rasgos de MobileNetV3
(ImageNet) más un histograma de color HSV, y se busca el vecino más parecido en
models/galeria/<marca>/*.jpg. Agregar un producto = agregar fotos a su carpeta.

    python ml/vision/identificar.py evaluar        # precisión por marca (validación cruzada)
"""

import re
import sys
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
GALERIA = RAIZ / "models" / "galeria"
INDICE = RAIZ / "models" / "galeria_indice.npz"
PESO_COLOR = 0.35
# Calibrado con productos de Open Food Facts que no están en la galería: con la
# carpeta _otros y 0.75, ~9 % de productos desconocidos reciben una marca.
UMBRAL = 0.75
NOMBRES_BONITOS = {
    "coca-cola": "Coca-Cola", "cocacola": "Coca-Cola", "coca-cola-light": "Coca-Cola Light",
    "chokis": "Chokis", "doritos": "Doritos", "gansito": "Gansito", "panditas": "Panditas",
    "sabritas": "Sabritas", "skittles": "Skittles", "oreo": "Oreo", "donas": "Donas Bimbo",
    "doraditas": "Doraditas", "cremax-chocolate": "Cremax Chocolate", "cremax-fresa": "Cremax Fresa",
    "takis": "Takis",
}


class Identificador:
    def __init__(self) -> None:
        import torch
        from torchvision.models import MobileNet_V3_Large_Weights, mobilenet_v3_large

        self.torch = torch
        pesos = MobileNet_V3_Large_Weights.IMAGENET1K_V2
        red = mobilenet_v3_large(weights=pesos)
        red.classifier = torch.nn.Identity()
        self.red = red.eval()
        self.media = np.array([0.485, 0.456, 0.406], np.float32)
        self.desv = np.array([0.229, 0.224, 0.225], np.float32)
        self.vectores, self.marcas = self._indice()

    # ----------------------------------------------------------- descriptores
    def describir(self, recortes_bgr: list[np.ndarray]) -> np.ndarray:
        if not recortes_bgr:
            return np.zeros((0, 1), np.float32)
        lote = []
        for bgr in recortes_bgr:
            rgb = cv2.cvtColor(cv2.resize(bgr, (224, 224), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
            lote.append(((rgb.astype(np.float32) / 255 - self.media) / self.desv).transpose(2, 0, 1))
        with self.torch.inference_mode():
            profundos = self.red(self.torch.from_numpy(np.stack(lote))).numpy()
        profundos /= np.linalg.norm(profundos, axis=1, keepdims=True) + 1e-8
        colores = np.stack([self._color(b) for b in recortes_bgr])
        return np.hstack([profundos * np.sqrt(1 - PESO_COLOR), colores * np.sqrt(PESO_COLOR)]).astype(np.float32)

    @staticmethod
    def _color(bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(cv2.resize(bgr, (96, 96)), cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [18, 4], [0, 180, 0, 256]).flatten()
        hist = np.sqrt(hist)  # Hellinger: menos peso a los colores dominantes
        return hist / (np.linalg.norm(hist) + 1e-8)

    # ----------------------------------------------------------- índice
    def _fotos(self) -> list[tuple[str, Path]]:
        return [(d.name, f) for d in sorted(GALERIA.iterdir()) if d.is_dir()
                for f in sorted(d.glob("*.jpg"))] if GALERIA.exists() else []

    def _indice(self) -> tuple[np.ndarray, np.ndarray]:
        fotos = self._fotos()
        firma = f"{len(fotos)}-{sum(f.stat().st_size for _, f in fotos)}"
        if INDICE.exists():
            datos = np.load(INDICE, allow_pickle=False)
            if str(datos["firma"]) == firma:
                return datos["vectores"], datos["marcas"]
        vectores, marcas = [], []
        for i in range(0, len(fotos), 64):
            bloque = fotos[i:i + 64]
            imgs = [cv2.imread(str(f)) for _, f in bloque]
            ok = [(m, im) for (m, _), im in zip(bloque, imgs) if im is not None]
            vectores.append(self.describir([im for _, im in ok]))
            marcas += [m for m, _ in ok]
        vectores = np.vstack(vectores) if vectores else np.zeros((0, 1), np.float32)
        marcas = np.array(marcas)
        if len(marcas):
            np.savez(INDICE, vectores=vectores, marcas=marcas, firma=firma)
        return vectores, marcas

    # ----------------------------------------------------------- consulta
    def identificar(self, recortes_bgr: list[np.ndarray], k: int = 5) -> list[tuple[str | None, float]]:
        if not len(self.marcas) or not recortes_bgr:
            return [(None, 0.0)] * len(recortes_bgr)
        similitud = self.describir(recortes_bgr) @ self.vectores.T
        salida = []
        for fila in similitud:
            vecinos = np.argsort(-fila)[:k]
            votos: dict[str, float] = {}
            for v in vecinos:
                votos[self.marcas[v]] = votos.get(self.marcas[v], 0) + float(fila[v])
            marca = max(votos, key=votos.get)
            score = float(fila[vecinos[0]]) if self.marcas[vecinos[0]] == marca else float(
                max(fila[v] for v in vecinos if self.marcas[v] == marca))
            # Carpetas que empiezan con "_" (p. ej. _otros) son ejemplos negativos.
            conocido = score >= UMBRAL and not marca.startswith("_")
            salida.append((NOMBRES_BONITOS.get(marca, marca.replace("-", " ").title()), score)
                          if conocido else (None, score))
        return salida


def evaluar() -> None:
    """Validación agrupada por foto original: ninguna foto aporta a galería y prueba a la vez."""
    ident = Identificador()
    fotos = ident._fotos()
    grupos = np.array([re.split(r"_jpg|_png|_jpeg", f.stem)[0] for _, f in fotos])
    rng = np.random.default_rng(0)
    prueba_grupos = set(rng.choice(np.unique(grupos), size=max(1, len(np.unique(grupos)) // 5), replace=False))
    es_prueba = np.array([g in prueba_grupos for g in grupos])
    vectores, marcas = ident.vectores, ident.marcas
    ident.vectores, ident.marcas = vectores[~es_prueba], marcas[~es_prueba]
    norm = lambda m: NOMBRES_BONITOS.get(m, m.replace("-", " ").title())
    aciertos, desconocidos, total = 0, 0, 0
    por_marca: dict[str, list[int]] = {}
    sim = vectores[es_prueba] @ ident.vectores.T
    for fila, real in zip(sim, marcas[es_prueba]):
        vecinos = np.argsort(-fila)[:5]
        votos: dict[str, float] = {}
        for v in vecinos:
            votos[ident.marcas[v]] = votos.get(ident.marcas[v], 0) + float(fila[v])
        pred = max(votos, key=votos.get)
        score = max(float(fila[v]) for v in vecinos if ident.marcas[v] == pred)
        total += 1
        stats = por_marca.setdefault(norm(real), [0, 0])
        stats[1] += 1
        if score < UMBRAL:
            desconocidos += 1
        elif norm(pred) == norm(real):
            aciertos += 1
            stats[0] += 1
    print(f"Prueba: {total} recortes | aciertos {aciertos / total:.1%} | sin identificar {desconocidos / total:.1%} | "
          f"errores {(total - aciertos - desconocidos) / total:.1%}")
    for marca, (a, t) in sorted(por_marca.items()):
        print(f"  {marca:18s} {a}/{t}")


if __name__ == "__main__":
    if sys.argv[1:] == ["evaluar"]:
        evaluar()
