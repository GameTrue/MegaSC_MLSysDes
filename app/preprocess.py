import logging
import math
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import List, Optional

from PIL import Image

logger = logging.getLogger(__name__)

from app.bpmn_extract import extract_bpmn_svg
from app.drawio_extract import extract_drawio_svg
from app.schemas import AnalyzeResponse

MAX_SIDE = 1024
HIRES_MAX_SIDE = 2560         # для крупных диаграмм (multi-tile → single high-res)
SVG_RENDER_SCALE = 2
_MAX_TILE_ASPECT = 4.0        # макс. соотношение сторон тайла
_TILE_OVERLAP = 0.15          # 15% перекрытие между тайлами
_MIN_DIM_AFTER_RESIZE = 400   # порог мин. стороны для grid-тайлинга
_GRID_TILE_TARGET = 1536      # целевой размер тайла для grid


def detect_format(file_bytes: bytes) -> str:
    if file_bytes[:5] == b"%PDF-":
        return "pdf"
    stripped = file_bytes.lstrip()
    if stripped[:4] == b"<svg" or (stripped[:5] == b"<?xml" and b"<svg" in stripped[:1024]):
        return "svg"
    return "image"


def pdf_to_images(file_bytes: bytes) -> tuple[List[Image.Image], Optional[str]]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    text_parts = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.open(BytesIO(pix.tobytes("png")))
        images.append(img)
        page_text = page.get_text("text").strip()
        if page_text:
            text_parts.append(page_text)
    doc.close()
    extracted = "\n".join(text_parts) if text_parts else None
    return images, extracted


def svg_to_image(file_bytes: bytes) -> Image.Image:
    import cairosvg

    png_bytes = cairosvg.svg2png(bytestring=file_bytes, scale=SVG_RENDER_SCALE)
    return Image.open(BytesIO(png_bytes))


def extract_svg_texts(file_bytes: bytes) -> Optional[str]:
    """Extract all text content from SVG, grouping <tspan> children under parent <text>."""
    try:
        root = ET.fromstring(file_bytes)
    except ET.ParseError:
        return None

    def _strip_ns(tag: str) -> str:
        return tag.split("}", 1)[1] if "}" in tag else tag

    blocks: list[str] = []
    for elem in root.iter():
        if _strip_ns(elem.tag) != "text":
            continue
        parts: list[str] = []
        if elem.text and elem.text.strip():
            parts.append(elem.text.strip())
        for child in elem:
            if _strip_ns(child.tag) == "tspan":
                t = (child.text or "").strip()
                if t:
                    parts.append(t)
        if parts:
            # join tspan parts and fix broken words (e.g. "Подтверждени е" → "Подтверждение")
            raw = " ".join(parts)
            raw = re.sub(r"(\w{2,}) (\w{1,2})\b(?= |$)", _maybe_join_suffix, raw)
            blocks.append(raw)

    if not blocks:
        return None
    # deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in blocks:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return "\n".join(unique)


_RU_SHORT_WORDS = {
    "и", "в", "с", "к", "о", "у", "а", "я", "на", "не", "но", "по", "до", "за", "из", "от",
    "ли", "ни", "же", "бы", "во", "ко",
}


def _maybe_join_suffix(m: re.Match) -> str:
    """Join 'Подтверждени е' → 'Подтверждение' but keep 'операции и' as is."""
    left, right = m.group(1), m.group(2)
    # Don't join if the short part is a known Russian word
    if right.lower() in _RU_SHORT_WORDS:
        return m.group(0)
    # Join: the short suffix is likely a broken word ending
    return left + right


def resize_image(image: Image.Image, max_side: int = MAX_SIDE) -> Image.Image:
    w, h = image.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        image = image.resize(new_size)
    return image


def ocr_extract_text(image: Image.Image) -> Optional[str]:
    """Извлечь текст из изображения через OCR (pytesseract)."""
    try:
        import pytesseract
    except ImportError:
        return None

    try:
        raw = pytesseract.image_to_string(image, lang="rus+eng")
    except Exception:
        logger.warning("OCR extraction failed", exc_info=True)
        return None

    if not raw or not raw.strip():
        return None

    lines = []
    seen: set[str] = set()
    for line in raw.strip().splitlines():
        line = line.strip()
        if len(line) < 2:
            continue
        if line not in seen:
            seen.add(line)
            lines.append(line)

    return "\n".join(lines) if lines else None


def _tile_strips(image: Image.Image, w: int, h: int) -> List[Image.Image]:
    """1D strip-тайлинг для extreme aspect ratio (>4.0)."""
    if w > h:
        # Широкая панорама → вертикальные разрезы
        tile_w = int(h * _MAX_TILE_ASPECT)
        stride = int(tile_w * (1 - _TILE_OVERLAP))
        tiles = []
        x = 0
        while x < w:
            x2 = min(x + tile_w, w)
            tile = image.crop((x, 0, x2, h))
            tiles.append(resize_image(tile))
            if x2 >= w:
                break
            x += stride
        return tiles
    else:
        # Высокая панорама → горизонтальные разрезы
        tile_h = int(w * _MAX_TILE_ASPECT)
        stride = int(tile_h * (1 - _TILE_OVERLAP))
        tiles = []
        y = 0
        while y < h:
            y2 = min(y + tile_h, h)
            tile = image.crop((0, y, w, y2))
            tiles.append(resize_image(tile))
            if y2 >= h:
                break
            y += stride
        return tiles


def _tile_grid(image: Image.Image, w: int, h: int) -> List[Image.Image]:
    """2D grid-тайлинг для крупных изображений с умеренным aspect ratio."""
    target = _GRID_TILE_TARGET
    overlap = int(target * _TILE_OVERLAP)
    stride = target - overlap

    cols = 1 + math.ceil((w - target) / stride) if w > target else 1
    rows = 1 + math.ceil((h - target) / stride) if h > target else 1

    tiles = []
    for iy in range(rows):
        for ix in range(cols):
            x1, y1 = ix * stride, iy * stride
            x2, y2 = min(x1 + target, w), min(y1 + target, h)
            # Последний тайл привязать к краю (чтобы не был слишком маленьким)
            if ix == cols - 1:
                x1 = max(0, w - target)
                x2 = w
            if iy == rows - 1:
                y1 = max(0, h - target)
                y2 = h
            tile = image.crop((x1, y1, x2, y2))
            tiles.append(resize_image(tile))
    return tiles


def stitch_tiles(tiles: List[Image.Image], orig_w: int, orig_h: int) -> Image.Image:
    """Склеить тайлы в одну картинку для отправки модели как single image."""
    if len(tiles) == 1:
        return tiles[0]

    n = len(tiles)
    tile_w, tile_h = tiles[0].size

    if n <= 4:
        # Простая линейная склейка по ориентации оригинала
        if orig_w >= orig_h:
            cols, rows = n, 1
        else:
            cols, rows = 1, n
    else:
        # Grid для большого числа тайлов
        aspect = orig_w / max(orig_h, 1)
        cols = max(1, round(math.sqrt(n * aspect)))
        rows = math.ceil(n / cols)

    canvas = Image.new("RGB", (cols * tile_w, rows * tile_h), (255, 255, 255))
    for idx, tile in enumerate(tiles):
        r, c = divmod(idx, cols)
        canvas.paste(tile, (c * tile_w, r * tile_h))

    return canvas


def prepare_tiles(image: Image.Image) -> List[Image.Image]:
    """Разбить изображение на читаемые тайлы (1D полосы или 2D сетка)."""
    w, h = image.size

    # Маленькое изображение — вернуть как есть
    if max(w, h) <= MAX_SIDE:
        return [resize_image(image)]

    aspect = max(w, h) / max(min(w, h), 1)

    # Extreme aspect → 1D strip тайлинг
    if aspect > _MAX_TILE_ASPECT:
        return _tile_strips(image, w, h)

    # Проверяем: хватит ли качества при обычном resize?
    scale = MAX_SIDE / max(w, h)
    if min(w, h) * scale >= _MIN_DIM_AFTER_RESIZE:
        return [resize_image(image)]

    # Крупное изображение с умеренным aspect → 2D grid
    return _tile_grid(image, w, h)


def load_image(file_bytes: bytes) -> tuple[List[Image.Image], Optional[str], Optional[AnalyzeResponse]]:
    """Returns (images, extracted_text, bpmn_response).

    bpmn_response is set when SVG is a bpmn-js diagram (model is bypassed).
    extracted_text is set for non-bpmn SVG files.
    """
    fmt = detect_format(file_bytes)
    logger.debug("Detected format: %s (%d bytes)", fmt, len(file_bytes))

    if fmt == "pdf":
        pages, extracted = pdf_to_images(file_bytes)
        return [resize_image(p.convert("RGB")) for p in pages], extracted, None

    if fmt == "svg":
        # Try programmatic BPMN extraction first
        bpmn_response = extract_bpmn_svg(file_bytes)
        img = svg_to_image(file_bytes)
        images = [resize_image(img.convert("RGB"))]
        if bpmn_response:
            return images, None, bpmn_response
        # Try draw.io programmatic extraction
        drawio_response = extract_drawio_svg(file_bytes)
        if drawio_response:
            return images, None, drawio_response
        # Fallback: model with extracted text hints
        extracted = extract_svg_texts(file_bytes)
        return images, extracted, None

    img = Image.open(BytesIO(file_bytes))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    extracted = ocr_extract_text(img)
    return [img], extracted, None
