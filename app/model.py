from functools import lru_cache
from typing import Dict, Any
import base64
from io import BytesIO

import httpx

from app.config import settings


@lru_cache(maxsize=1)
def get_model_bundle() -> Dict[str, Any]:
    if settings.use_dummy or settings.use_lmstudio:
        return {"model": None, "processor": None}

    import torch
    from transformers import AutoProcessor, AutoModelForCausalLM, BitsAndBytesConfig

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
    return {"model": model, "processor": processor}


def infer(image, prompt: str, extracted_text: str | None = None) -> str:
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

    # LM Studio path (OpenAI-compatible vision API)
    if settings.use_lmstudio:
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        headers = {"Content-Type": "application/json"}
        if settings.lmstudio_token:
            headers["Authorization"] = f"Bearer {settings.lmstudio_token}"

        full_prompt = prompt
        if extracted_text:
            full_prompt += (
                "\n\nИз файла извлечён следующий текст (используй его как ТОЧНЫЙ справочник"
                " — копируй эти строки дословно в поле action):\n"
                "---\n" + extracted_text + "\n---"
            )

        payload = {
            "model": settings.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": full_prompt},
                    ],
                },
            ],
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_new_tokens,
        }

        url = f"{settings.lmstudio_base_url}/v1/chat/completions"
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=settings.request_timeout)
        except httpx.TimeoutException:
            raise RuntimeError("LM Studio timeout. Increase REQUEST_TIMEOUT or reduce MAX_NEW_TOKENS.")

        resp.raise_for_status()
        data = resp.json()
        if "choices" in data and data["choices"]:
            return data["choices"][0].get("message", {}).get("content", "") or data["choices"][0].get("text", "")
        raise RuntimeError(f"Unexpected LM Studio response format: {data}")

    # Local HF model path
    import torch

    bundle = get_model_bundle()
    model = bundle["model"]
    processor = bundle["processor"]
    first_param_device = next(model.parameters()).device
    inputs = processor(images=image, text=prompt, return_tensors="pt")
    inputs = {k: v.to(first_param_device) for k, v in inputs.items()}
    generation = model.generate(
        **inputs,
        max_new_tokens=settings.max_new_tokens,
        temperature=settings.temperature,
        top_p=settings.top_p,
        do_sample=True,
    )
    output = processor.batch_decode(generation, skip_special_tokens=True)[0]
    return output
