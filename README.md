## Diagram Analyzer Service

Реализация сервиса из чек-поинта: загрузка изображения BPMN/блок-схемы → описание алгоритма в структурированном JSON. Стек: FastAPI + Qwen2.5-VL-7B (4-bit) + Gradio демо.

### Быстрый старт (GPU < 8GB)

```bash
docker compose up --build
```

Откроется:
- REST: `http://localhost:8000/api/analyze` (Swagger на `/docs`)
- Gradio демо: `http://localhost:7860`

### Запуск локально (без Docker)

```bash
python -m venv .venv
. .venv/bin/activate  # или .venv\Scripts\activate в Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Быстрый smoke-тест без скачивания модели:

```bash
USE_DUMMY=1 PYTHONPATH=. python scripts/benchmark.py --dir "Диаграммы. 2 часть/Диаграммы. 2 часть/Picture" --limit 2
```

Для реальной модели снимите `USE_DUMMY` и задайте `MODEL_NAME` на нужный чекпоинт.

### Переменные окружения

- `MODEL_NAME` — по умолчанию `Qwen/Qwen2-VL-7B-Instruct`
- `DEVICE` — `cuda` или `cpu` (авто)
- `ENABLE_BNB_INT4` — `1` включает 4-bit quant (bitsandbytes)
- `USE_DUMMY` — `1` включает заглушку для быстрых тестов без загрузки модели
- `USE_LMSTUDIO` — `1` чтобы слать запросы в LM Studio
- `LMSTUDIO_BASE_URL` — база API, напр. `http://192.168.8.152:22227`
- `LMSTUDIO_TOKEN` — токен LM Studio (если включена авторизация)
- `HF_TOKEN` — при необходимости для скачивания модели из HF

### Работа через LM Studio

```bash
export USE_LMSTUDIO=1
export LMSTUDIO_BASE_URL=http://192.168.8.152:22227
export LMSTUDIO_TOKEN=sk-lm-rilUANNP:hoPFUWniuW2FPs6c4Gvg
export MODEL_NAME=qwen/qwen2.5-vl-7b
PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000
PYTHONPATH=. python scripts/benchmark.py --dir "Диаграммы. 2 часть/Диаграммы. 2 часть/Picture" --limit 3
```
Препроцессинг автоматически конвертирует изображение в RGB и уменьшает длинную сторону до 768px — это избавляет LM Studio от ошибок 500.

### Пример запроса

```bash
curl -X POST http://localhost:8000/api/analyze \
  -F "file=@sample.png"
```

Ответ:
```json
{
  "diagram_type": "bpmn",
  "description": "Онбординг пользователя",
  "steps": [
    {"step":1,"action":"Пользователь отправляет заявку","role":"Client"},
    {"step":2,"action":"Система проверяет данные","role":"Backend"}
  ]
}
```

### Benchmark

```bash
python scripts/benchmark.py --dir "Диаграммы. 2 часть/Диаграммы. 2 часть/Picture" --limit 5
```

### Слайд-дек / демо

Скелет презентации см. `presentation_outline.md`. Демо — запускайте Gradio и прогоните несколько образцов из папки `Диаграммы. 2 часть/Диаграммы. 2 часть/Picture`.

### CPU-режим

`DEVICE=cpu` и `ENABLE_BNB_INT4=0` — медленнее, но без GPU. Для GGUF/llama.cpp потребуется заменить загрузчик в `app/model.py`.
