from pathlib import Path
from pydantic import BaseModel
import os


class Settings(BaseModel):
    model_config = {"protected_namespaces": ()}

    model_id: str = os.getenv("MODEL_NAME", "Qwen/Qwen2-VL-7B-Instruct")
    device: str = os.getenv("DEVICE", "cuda" if os.getenv("CUDA_VISIBLE_DEVICES", "") != "" else "cpu")
    max_new_tokens: int = int(os.getenv("MAX_NEW_TOKENS", "10000"))
    temperature: float = float(os.getenv("TEMPERATURE", "0"))
    top_p: float = float(os.getenv("TOP_P", "0.9"))
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "300"))
    hf_token: str | None = os.getenv("HF_TOKEN")
    cache_dir: Path = Path(os.getenv("HF_CACHE", "~/.cache/huggingface")).expanduser()
    enable_bnb_int4: bool = os.getenv("ENABLE_BNB_INT4", "1") == "1"
    use_dummy: bool = os.getenv("USE_DUMMY", "0") == "1"
    lmstudio_base_url: str = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:22227")
    lmstudio_token: str | None = os.getenv("LMSTUDIO_TOKEN")
    use_lmstudio: bool = os.getenv("USE_LMSTUDIO", "").lower() in {"1", "true", "yes"}


settings = Settings()
