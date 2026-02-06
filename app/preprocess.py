import re
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import List, Optional

from PIL import Image

MAX_SIDE = 1024
SVG_RENDER_SCALE = 2


def detect_format(file_bytes: bytes) -> str:
    if file_bytes[:5] == b"%PDF-":
        return "pdf"
    stripped = file_bytes.lstrip()
    if stripped[:4] == b"<svg" or (stripped[:5] == b"<?xml" and b"<svg" in stripped[:1024]):
        return "svg"
    return "image"


def pdf_to_images(file_bytes: bytes) -> List[Image.Image]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.open(BytesIO(pix.tobytes("png")))
        images.append(img)
    doc.close()
    return images


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


def resize_image(image: Image.Image) -> Image.Image:
    w, h = image.size
    if max(w, h) > MAX_SIDE:
        scale = MAX_SIDE / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        image = image.resize(new_size)
    return image


def load_image(file_bytes: bytes) -> tuple[List[Image.Image], Optional[str]]:
    """Returns (images, extracted_text). extracted_text is set for SVG files."""
    fmt = detect_format(file_bytes)

    if fmt == "pdf":
        pages = pdf_to_images(file_bytes)
        return [resize_image(p.convert("RGB")) for p in pages], None

    if fmt == "svg":
        img = svg_to_image(file_bytes)
        extracted = extract_svg_texts(file_bytes)
        return [resize_image(img.convert("RGB"))], extracted

    img = Image.open(BytesIO(file_bytes))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return [resize_image(img)], None
