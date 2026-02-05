from PIL import Image
from io import BytesIO

SUPPORTED_FORMATS = {"JPEG", "JPG", "PNG", "WEBP"}


def load_image(file_bytes: bytes) -> Image.Image:
    image = Image.open(BytesIO(file_bytes)).convert("RGB")
    if image.format and image.format.upper() not in SUPPORTED_FORMATS:
        image = image.convert("RGB")
    # resize to fit within 1024 px on the longest side to stay within LM Studio limits
    max_side = 768
    w, h = image.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        image = image.resize(new_size)
    return image
