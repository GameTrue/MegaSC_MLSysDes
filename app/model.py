import logging
from functools import lru_cache
from typing import Dict, Any
import base64
from io import BytesIO

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_model_bundle() -> Dict[str, Any]:
    if settings.use_dummy or settings.use_lmstudio:
        logger.info("Using %s mode, skipping model load", "dummy" if settings.use_dummy else "LM Studio")
        return {"model": None, "processor": None}

    import torch
    from transformers import AutoProcessor, AutoModelForCausalLM, BitsAndBytesConfig
    logger.info("Loading model %s on %s (int4=%s)", settings.model_id, settings.device, settings.enable_bnb_int4)

    quantization_config = None
    if settings.enable_bnb_int4 and settings.device.startswith("cuda"):
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

    model = AutoModelForCausalLM.from_pretrained(
        settings.model_id,
        device_map="auto",
        torch_dtype=torch.bfloat16 if settings.device.startswith("cuda") else torch.float32,
        trust_remote_code=True,
        quantization_config=quantization_config,
        token=settings.hf_token,
        cache_dir=settings.cache_dir,
    )
    processor = AutoProcessor.from_pretrained(
        settings.model_id,
        trust_remote_code=True,
        token=settings.hf_token,
        cache_dir=settings.cache_dir,
    )
    logger.info("Model loaded successfully")
    return {"model": model, "processor": processor}


def _build_lmstudio_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if settings.lmstudio_token:
        headers["Authorization"] = f"Bearer {settings.lmstudio_token}"
    return headers


def _parse_lmstudio_response(data: dict) -> str:
    if "choices" in data and data["choices"]:
        return data["choices"][0].get("message", {}).get("content", "") or data["choices"][0].get("text", "")
    raise RuntimeError(f"Unexpected LM Studio response format: {data}")


def _build_vision_payload(image, prompt: str, extracted_text: str | None = None) -> dict:
    images = image if isinstance(image, list) else [image]
    is_tiled = len(images) > 1

    full_prompt = prompt
    if is_tiled:
        full_prompt = (
            f"Это одна диаграмма, разделённая на {len(images)} частей с перекрытием. "
            "Проанализируй как единое целое.\n\n" + full_prompt
        )
    if extracted_text:
        full_prompt += (
            "\n\nИз файла извлечён следующий текст (используй его как ТОЧНЫЙ справочник"
            " — копируй эти строки дословно в поле action):\n"
            "---\n" + extracted_text + "\n---"
        )

    content = []
    for img in images:
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    content.append({"type": "text", "text": full_prompt})

    return {
        "model": settings.model_id,
        "messages": [{"role": "user", "content": content}],
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "max_tokens": settings.max_new_tokens,
    }


async def infer(image, prompt: str, extracted_text: str | None = None) -> str:
    # Dummy path for CI
    if settings.use_dummy:
        return """
{
  "diagram_type": "bpmn",
  "description": "Dummy response for CI",
  "steps": [
    {"step": 1, "action": "Load image", "role": "system"},
    {"step": 2, "action": "Return placeholder", "role": "model"}
  ]
}
""".strip()

    # LM Studio path — async, cancellable on client disconnect
    if settings.use_lmstudio:
        payload = _build_vision_payload(image, prompt, extracted_text)
        url = f"{settings.lmstudio_base_url}/v1/chat/completions"
        logger.debug("LM Studio request to %s (model=%s)", url, settings.model_id)
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=_build_lmstudio_headers())
            except httpx.TimeoutException:
                logger.error("LM Studio timeout after %ds", settings.request_timeout)
                raise RuntimeError("LM Studio timeout. Increase REQUEST_TIMEOUT or reduce MAX_NEW_TOKENS.")
        if resp.status_code != 200:
            body = resp.text[:500]
            logger.error("LM Studio error %d: %s", resp.status_code, body)
            resp.raise_for_status()
        result = _parse_lmstudio_response(resp.json())
        logger.debug("LM Studio response: %d chars", len(result))
        return result

    # Local HF model path (sync — runs in threadpool via asyncio)
    import asyncio
    return await asyncio.to_thread(_infer_local_hf, image, prompt)


async def infer_text(prompt: str) -> str:
    """Text-only inference (no image). Used for diagram generation."""
    if settings.use_dummy:
        return "graph TD\n    A([\"Начало\"]) --> B[\"Задача\"]\n    B --> C([\"Конец\"])"

    if settings.use_lmstudio:
        payload = {
            "model": settings.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_new_tokens,
        }
        url = f"{settings.lmstudio_base_url}/v1/chat/completions"
        logger.debug("LM Studio text request to %s", url)
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=_build_lmstudio_headers())
            except httpx.TimeoutException:
                logger.error("LM Studio text inference timeout after %ds", settings.request_timeout)
                raise RuntimeError("LM Studio timeout")
        resp.raise_for_status()
        return _parse_lmstudio_response(resp.json())

    import asyncio
    return await asyncio.to_thread(_infer_text_local_hf, prompt)


def _infer_local_hf(image, prompt: str) -> str:
    import torch

    single_image = image[0] if isinstance(image, list) else image
    bundle = get_model_bundle()
    mdl = bundle["model"]
    processor = bundle["processor"]
    first_param_device = next(mdl.parameters()).device
    inputs = processor(images=single_image, text=prompt, return_tensors="pt")
    inputs = {k: v.to(first_param_device) for k, v in inputs.items()}
    generation = mdl.generate(
        **inputs,
        max_new_tokens=settings.max_new_tokens,
        temperature=settings.temperature,
        top_p=settings.top_p,
        do_sample=True,
    )
    return processor.batch_decode(generation, skip_special_tokens=True)[0]


def _infer_text_local_hf(prompt: str) -> str:
    import torch

    bundle = get_model_bundle()
    mdl = bundle["model"]
    processor = bundle["processor"]
    first_param_device = next(mdl.parameters()).device
    inputs = processor(text=prompt, return_tensors="pt")
    inputs = {k: v.to(first_param_device) for k, v in inputs.items()}
    generation = mdl.generate(
        **inputs,
        max_new_tokens=settings.max_new_tokens,
        temperature=settings.temperature,
        top_p=settings.top_p,
        do_sample=True,
    )
    return processor.batch_decode(generation, skip_special_tokens=True)[0]
