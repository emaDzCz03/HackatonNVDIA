"""Genera los PNG de la PWA a partir de la ilustración de inventario.

Intenta cairosvg. Si no está instalado, rasteriza el SVG con Pillow.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "frontend" / "assets" / "images" / "inventory-illustration.svg"
ICON_DIR = ROOT / "frontend" / "assets" / "icons"
PAPER = (245, 247, 244, 255)
VIEWBOX = (600, 500)

TOKEN = re.compile(r"[MmLlHhVvZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")


def parse_color(value: str | None) -> tuple[int, int, int, int] | None:
    if not value or value == "none":
        return None
    value = value.strip()
    if not value.startswith("#"):
        return None
    hex_color = value[1:]
    if len(hex_color) == 3:
        hex_color = "".join(channel * 2 for channel in hex_color)
    channels = tuple(int(hex_color[index : index + 2], 16) for index in (0, 2, 4))
    return (*channels, 255)


def path_subpaths(d: str) -> list[list[tuple[float, float]]]:
    tokens = TOKEN.findall(d)
    index = 0
    command = ""
    cursor_x = cursor_y = 0.0
    start_x = start_y = 0.0
    subpaths: list[list[tuple[float, float]]] = []
    points: list[tuple[float, float]] = []

    def number() -> float:
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        if re.fullmatch(r"[MmLlHhVvZz]", tokens[index]):
            command = tokens[index]
            index += 1

        if command in "Mm":
            if points:
                subpaths.append(points)
                points = []
            x, y = number(), number()
            if command == "m":
                x += cursor_x
                y += cursor_y
            cursor_x, cursor_y = x, y
            start_x, start_y = x, y
            points = [(cursor_x, cursor_y)]
            command = "L" if command == "M" else "l"
            while index < len(tokens) and not re.fullmatch(r"[MmLlHhVvZz]", tokens[index]):
                x, y = number(), number()
                if command == "l":
                    x += cursor_x
                    y += cursor_y
                cursor_x, cursor_y = x, y
                points.append((cursor_x, cursor_y))
        elif command in "Ll":
            x, y = number(), number()
            if command == "l":
                x += cursor_x
                y += cursor_y
            cursor_x, cursor_y = x, y
            points.append((cursor_x, cursor_y))
        elif command in "Hh":
            x = number()
            if command == "h":
                x += cursor_x
            cursor_x = x
            points.append((cursor_x, cursor_y))
        elif command in "Vv":
            y = number()
            if command == "v":
                y += cursor_y
            cursor_y = y
            points.append((cursor_x, cursor_y))
        elif command in "Zz":
            points.append((start_x, start_y))
            subpaths.append(points)
            points = []
            cursor_x, cursor_y = start_x, start_y
        else:
            raise ValueError(f"Comando SVG no soportado: {command}")

    if points:
        subpaths.append(points)
    return subpaths


def render_with_pillow(size: int, content_scale: float):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), PAPER)
    draw = ImageDraw.Draw(image)
    fit = min(size / VIEWBOX[0], size / VIEWBOX[1]) * content_scale
    offset_x = (size - VIEWBOX[0] * fit) / 2
    offset_y = (size - VIEWBOX[1] * fit) / 2

    def project(point: tuple[float, float]) -> tuple[float, float]:
        return (offset_x + point[0] * fit, offset_y + point[1] * fit)

    root = ET.parse(SVG_PATH).getroot()
    for element in root:
        tag = element.tag.rsplit("}", 1)[-1]
        fill = parse_color(element.get("fill"))
        stroke = parse_color(element.get("stroke"))
        if tag == "ellipse":
            cx = float(element.get("cx", 0))
            cy = float(element.get("cy", 0))
            rx = float(element.get("rx", 0))
            ry = float(element.get("ry", 0))
            box = [project((cx - rx, cy - ry)), project((cx + rx, cy + ry))]
            draw.ellipse(box, fill=fill)
        elif tag == "circle":
            cx = float(element.get("cx", 0))
            cy = float(element.get("cy", 0))
            radius = float(element.get("r", 0))
            box = [project((cx - radius, cy - radius)), project((cx + radius, cy + radius))]
            draw.ellipse(box, fill=fill)
        elif tag == "path":
            width = max(1, round(float(element.get("stroke-width", 1)) * fit))
            for subpath in path_subpaths(element.get("d", "")):
                mapped = [project(point) for point in subpath]
                closed = len(subpath) > 2 and subpath[0] == subpath[-1]
                if fill and closed:
                    draw.polygon(mapped, fill=fill)
                elif stroke and len(mapped) >= 2:
                    draw.line(mapped, fill=stroke, width=width, joint="curve")
        else:
            raise ValueError(f"Elemento SVG no soportado: {tag}")
    return image


def render_with_cairosvg(size: int, content_scale: float):
    import cairosvg
    from PIL import Image

    png = cairosvg.svg2png(
        url=str(SVG_PATH),
        output_width=VIEWBOX[0],
        output_height=VIEWBOX[1],
    )
    source = Image.open(__import__("io").BytesIO(png)).convert("RGBA")
    image = Image.new("RGBA", (size, size), PAPER)
    target = max(1, round(size * content_scale))
    scale = min(target / source.width, target / source.height)
    resized = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))))
    image.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2), resized)
    return image


def render_icon(size: int, content_scale: float):
    try:
        return render_with_cairosvg(size, content_scale)
    except ImportError, OSError:
        return render_with_pillow(size, content_scale)


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    icons = {
        "icon-192.png": (192, 0.86),
        "icon-512.png": (512, 0.86),
        "icon-512-maskable.png": (512, 0.62),
    }
    for filename, (size, scale) in icons.items():
        render_icon(size, scale).save(ICON_DIR / filename)
        print(f"Generado {ICON_DIR / filename}")


if __name__ == "__main__":
    main()
