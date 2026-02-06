# Diagram Analyzer

Сервис распознавания диаграмм: загрузка изображения BPMN / блок-схемы / UML → структурированное описание алгоритма в JSON. Обратная задача: текст → Mermaid-диаграмма.

**Трек:** ML System Design, Мегашкола ИТМО x Nexign 2026

## Архитектура

```
                    POST /api/analyze (file)
                               │
                   ┌───────────▼───────────┐
                   │    FastAPI Gateway     │
                   │   валидация, logging   │
                   └───────────┬───────────┘
                               │
                   ┌───────────▼───────────┐
                   │    Препроцессинг      │
                   │  detect_format →      │
                   │  PDF: PyMuPDF+текст   │
                   │  SVG: CairoSVG+render │
                   │  Image: Pillow+OCR    │
                   └───────────┬───────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
               SVG с bpmn-js        Всё остальное
               или draw.io?        (PDF / Image /
                    │                прочие SVG)
                    ▼                     │
             ┌────────────┐               │
             │ Извлечение │               ▼
             │ графа из   │    ┌───────────────────┐
             │ XML        │    │  prepare_tiles()   │
             │ (без VLM)  │    │  + extracted_text  │
             └─────┬──────┘    │  VLM Inference     │
                   │           │  Qwen3-VL-8B       │
                   │           └─────────┬─────────┘
                   │                     │
                   │           ┌─────────▼─────────┐
                   │           │   to_response()    │
                   │           │   JSON-парсинг     │
                   │           └─────────┬─────────┘
                   │                     │
                   └──────────┬──────────┘
                              ▼
                       JSON Response
```

**Два пути обработки:**
- **Программный (bpmn-js / draw.io SVG):** граф извлекается из XML-метаданных SVG без модели → мгновенный точный результат, `to_response()` не вызывается
- **VLM (всё остальное):** тайлинг → модель → парсинг JSON из текстового выхода

### Ключевые решения

| Решение | Зачем |
|---|---|
| **Программные парсеры BPMN/draw.io** | SVG из bpmn-js и draw.io содержат полные метаданные графа — извлекаем структуру из XML без модели. Нет галлюцинаций, мгновенный ответ |
| **Тайлинг (1D strips + 2D grid)** | Панорамные диаграммы (aspect ratio >4) при resize теряют текст. Разбиваем на тайлы с 15% overlap |
| **OCR-подсказка (pytesseract)** | Текст извлекается на этапе препроцессинга и передаётся модели как «справочник» — модель копирует дословно, а не угадывает |
| **Async inference (httpx.AsyncClient)** | При disconnect клиента FastAPI отменяет asyncio-задачу → httpx закрывает TCP → LM Studio прекращает генерацию |
| **Few-shot в промпте** | 2 примера (простая блок-схема + развилка) стабилизируют JSON-формат ответа |

## Возможности

- Форматы входа: **PNG, JPEG, WebP, PDF (многостраничный), SVG (bpmn-js, draw.io, произвольные)**
- Распознавание: шаги, связи (next_steps), типы узлов (start/end/task/decision/subprocess), swim lanes (роли)
- Обратная задача: текст → Mermaid-диаграмма (`POST /api/generate`)
- Web UI с таблицей шагов, JSON-просмотром, превью изображения и Mermaid-рендером
- Пакетная обработка (`POST /api/analyze/batch`)
- Экспорт результатов в CSV

## Быстрый старт

### Предварительные требования

Перед сборкой и запуском сервиса необходимо запустить модель в **LM Studio**:

1. Откройте LM Studio и загрузите модель `Qwen3-VL-8B` (или совместимую VLM)
2. Запустите локальный сервер в LM Studio (вкладка «Local Server»)
3. Скопируйте адрес сервера (например, `http://192.168.1.10:1234`) и API-ключ
4. Укажите эти значения в файле `.env` (см. ниже)

### Docker

```bash
# 1. Создать .env с ключами LM Studio
cat > .env << 'EOF'
USE_LMSTUDIO=1
LMSTUDIO_BASE_URL=http://<YOUR_HOST>:<PORT>
LMSTUDIO_TOKEN=<YOUR_TOKEN>
MODEL_NAME=qwen/qwen3-vl-8b
EOF

# 2. Запустить
docker compose up --build
```

### Локально (без Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

После запуска:
- **Web UI:** http://localhost:8000
- **Swagger:** http://localhost:8000/docs
- **Health:** http://localhost:8000/health

## API

### `POST /api/analyze` — распознать диаграмму

```bash
curl -X POST http://localhost:8000/api/analyze -F "file=@diagram.png"
```

Ответ (одна страница):
```json
{
  "diagram_type": "bpmn",
  "description": "Процесс обработки заявки",
  "steps": [
    {
      "step": "start",
      "action": "",
      "type": "start",
      "role": "Клиент",
      "next_steps": [{"to": 1, "label": ""}]
    },
    {
      "step": 1,
      "action": "Отправить заявку",
      "type": "task",
      "role": "Клиент",
      "next_steps": [{"to": 2, "label": ""}]
    },
    {
      "step": 2,
      "action": "Проверить данные",
      "type": "decision",
      "role": "Система",
      "next_steps": [
        {"to": 3, "label": "Да"},
        {"to": 4, "label": "Нет"}
      ]
    }
  ]
}
```

Ответ (многостраничный PDF):
```json
{
  "pages": [
    {"page": 1, "diagram_type": "...", "steps": [...]},
    {"page": 2, "diagram_type": "...", "steps": [...]}
  ]
}
```

### `POST /api/analyze/batch` — пакетная обработка

```bash
curl -X POST http://localhost:8000/api/analyze/batch \
  -F "files=@diagram1.png" -F "files=@diagram2.svg"
```

### `POST /api/generate` — текст → Mermaid-диаграмма

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Пользователь отправляет заявку. Система проверяет. Если ОК — одобряем, иначе отклоняем."}'
```

### `GET /health` — статус сервиса

```json
{"status": "ok", "model": "qwen/qwen3-vl-8b", "device": "cpu"}
```

## Конфигурация

Переменные окружения (файл `.env` или export):

| Переменная | По умолчанию | Описание |
|---|---|---|
| `USE_LMSTUDIO` | `0` | `1` — inference через LM Studio (удалённо) |
| `LMSTUDIO_BASE_URL` | `http://localhost:22227` | Адрес LM Studio API |
| `LMSTUDIO_TOKEN` | — | Bearer-токен LM Studio |
| `MODEL_NAME` | `Qwen/Qwen3-VL-8B` | ID модели |
| `TEMPERATURE` | `0` | Температура генерации |
| `MAX_NEW_TOKENS` | `10000` | Макс. длина ответа |
| `REQUEST_TIMEOUT` | `300` | Таймаут запроса к LM Studio (сек) |
| `USE_DUMMY` | `0` | `1` — заглушка для тестов без модели |

## Метрики качества

Оценка на тестовом наборе из 11 диаграмм (`scripts/evaluate.py`):

| Метрика | Значение |
|---|---|
| Node Precision (avg) | **0.851** |
| Node Recall (avg) | **0.916** |
| Action Similarity (avg) | **0.898** |
| Latency p50 | **18.2 сек** |
| Latency p95 | **235.5 сек** |

```bash
# Прогон оценки
python -m scripts.evaluate \
  --test-dir "test" \
  --api-url http://localhost:8000/api/analyze \
  --format table
```

## Бенчмарк

```bash
# С моделью
PYTHONPATH=. python scripts/benchmark.py --dir "path/to/images" --limit 5

# Smoke-тест (без модели)
USE_DUMMY=1 PYTHONPATH=. python scripts/benchmark.py --dir "path/to/images" --limit 2
```

## Структура проекта

```
app/
├── main.py              # FastAPI-приложение, эндпоинты
├── model.py             # Inference: LM Studio (async) / dummy
├── preprocess.py        # Определение формата, PDF/SVG конвертация, тайлинг, OCR
├── postprocess.py       # Парсинг JSON из текстового выхода модели
├── prompt.py            # Промпты для анализа и генерации Mermaid
├── schemas.py           # Pydantic-модели (Step, AnalyzeResponse, GenerateRequest)
├── config.py            # Pydantic Settings из env-переменных
├── bpmn_extract.py      # Программное извлечение графа из bpmn-js SVG
├── drawio_extract.py    # Программное извлечение графа из draw.io SVG
├── ui.py                # Отдача HTML-шаблона
├── demo.py              # Gradio-интерфейс (альтернативный UI)
└── static/
    └── index.html       # Web UI (single-page, Mermaid.js)

scripts/
├── benchmark.py         # Прогон модели на наборе изображений
└── evaluate.py          # Сравнение с ground truth, метрики

Dockerfile               # Docker-образ (~200 МБ)
docker-compose.yml       # docker compose up --build
requirements.txt         # Python-зависимости
```

## Технологический стек

| Компонент | Технология | Лицензия |
|---|---|---|
| VLM | Qwen3-VL-8B | Apache 2.0 |
| Inference runtime | LM Studio (удалённо) | — |
| API | FastAPI + Uvicorn | MIT |
| Препроцессинг | Pillow, CairoSVG, PyMuPDF, pytesseract | MIT / LGPL / AGPL |
| Валидация | Pydantic | MIT |
| HTTP-клиент | httpx (async) | BSD |
| UI | HTML + Mermaid.js | MIT |
| Контейнеризация | Docker + docker-compose | Apache 2.0 |

## Sizing

| Параметр | Значение |
|---|---|
| Docker image | ~200 MB |
| RAM сервиса | ~500 MB |
| VRAM (на машине с LM Studio) | ~5–6 GB |
| Время / изображение | 10–20 сек |
| Требование ТЗ: < 20 сек | Выполняется |
