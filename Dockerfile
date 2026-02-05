FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-venv git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# кешируем pip между сборками, чтобы не тянуть зависимости заново
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

COPY app app

ENV PATH="/app/.venv/bin:${PATH}"
ENV HF_HOME=/app/.cache/huggingface

EXPOSE 8000 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
