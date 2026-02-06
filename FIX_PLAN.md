# Полный план правок — Diagram Analyzer

Ветка: `dev-ivan`

---

## Часть A — Баг-фиксы

### ~~A0. [CRITICAL] `app/model.py` — индентация LM Studio~~ DONE (ef85644)
### ~~A0b. [MEDIUM] `app/model.py:98` — timeout в fallback~~ DONE (ef85644)

### ~~A1. [CRITICAL] `app/main.py:18` — `settings.model_name` → `settings.model_id`~~ DONE
Эндпоинт `/health` падает с `AttributeError`.

### ~~A2. [HIGH] `app/config.py:22` — убрать неявное включение LM Studio~~ DONE
Оставить только `os.getenv("USE_LMSTUDIO", "").lower() in {"1", "true", "yes"}`.

### ~~A3. [HIGH] `.gitignore` — добавить `.env`, убрать дубликат `__pycache__`~~ DONE

### ~~A4. [HIGH] `app/static/index.html:127` — XSS: `innerHTML` → `textContent`~~ DONE
Создать `<pre>` через DOM API. Сделано вместе с частью B (шаг 9).

### ~~A5. [MEDIUM] `app/postprocess.py:17` — regex квотирования ключей~~ DONE
Уточнить: `re.sub(r'(?<=[{,\[])\s*(\w+)\s*:', r' "\1":', snippet)`.

### ~~A6. [MEDIUM] `app/prompt.py:8` — `"id"` → `"step"`~~ DONE

### ~~A7. [MEDIUM] `app/preprocess.py:7-10` — проверять формат до `convert("RGB")`~~ DONE
Переработано в части D (шаг 7).

### ~~A8. [LOW] `docker-compose.yml` — удалить `version: "3.9"`~~ DONE
Переработан в части C (шаг 13).

### ~~A9. [LOW] `Examples/46..json` → `Examples/46.json`~~ DONE

### ~~A10. [LOW] `requirements.txt` — оптимизация torch~~ DONE
Решено в части C (шаг 11).

---

## ~~Часть B — Кнопка «Остановить» в UI~~ DONE

**Файл:** `app/static/index.html`

Изменения:
1. ~~Добавить кнопку «Остановить» рядом с «Отправить» (скрыта по умолчанию)~~ DONE
2. ~~Хранить массив `AbortController` для каждого запроса~~ DONE
3. ~~По клику — `abort()` на всех контроллерах~~ DONE
4. ~~В `processFile()` — `signal: controller.signal` в `fetch()`~~ DONE
5. ~~Ловить `AbortError` → статус «отменено»~~ DONE
6. ~~Показывать/скрывать кнопки при старте/окончании обработки~~ DONE

Серверные изменения не нужны — `fetch` abort закрывает соединение, FastAPI/httpx прерывают запрос.

---

## ~~Часть C — Два Dockerfile (лёгкий + GPU)~~ DONE

### Новые/изменённые файлы:

**`requirements.txt`** — базовые зависимости (без torch):
```
fastapi==0.111.0
uvicorn[standard]==0.30.1
pydantic==2.7.0
python-multipart==0.0.9
pillow==10.3.0
httpx==0.27.0
numpy==1.26.4
gradio==4.24.0
PyMuPDF==1.25.3
cairosvg==2.7.1
```

**`requirements-gpu.txt`** — GPU-зависимости:
```
-r requirements.txt
--extra-index-url https://download.pytorch.org/whl/cu121
torch==2.2.2+cu121
transformers==4.46.2
accelerate==0.30.1
bitsandbytes==0.43.1
```

**`Dockerfile`** — лёгкий (LM Studio, ~200МБ):
- Базовый образ: `python:3.11-slim`
- apt: `libcairo2` (для cairosvg)
- `pip install --no-cache-dir -r requirements.txt`

**`Dockerfile.gpu`** — тяжёлый (локальный инференс):
- Базовый образ: `nvidia/cuda:12.4.1-runtime-ubuntu22.04` (обновлённый)
- apt: `python3-pip python3-venv git libcairo2`
- venv + `pip install --no-cache-dir -r requirements-gpu.txt`

**`docker-compose.yml`** — лёгкий вариант:
```yaml
services:
  app:
    build: .
    env_file: [.env]
    ports: ["8000:8000", "7860:7860"]
```

**`docker-compose.gpu.yml`** — GPU вариант:
```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile.gpu
    env_file: [.env]
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
    ports: ["8000:8000", "7860:7860"]
```

**`app/model.py`** — условный импорт torch/transformers:
- Перенести `import torch`, `from transformers import ...` внутрь `get_model_bundle()` и локального пути `infer()`
- Верхнеуровневые импорты: только `httpx`, `base64`, `BytesIO`

---

## ~~Часть D — Поддержка PDF и SVG~~ DONE

### Файлы:

**`app/preprocess.py`** — полная переработка:
- `detect_format(file_bytes)` — определить формат по magic bytes (`%PDF`, `<svg`, иначе image)
- `pdf_to_images(file_bytes)` → `list[Image]` через PyMuPDF (fitz), DPI=200
- `svg_to_image(file_bytes)` → `Image` через cairosvg
- `resize_image(image)` — вынести ресайз в отдельную функцию
- `load_image(file_bytes)` → `list[Image]` (для PDF несколько страниц, для остальных — одна)
- Проверка формата изображения до `convert("RGB")` (фикс A7)

**`app/main.py`** — обработка нескольких страниц:
- `analyze()`: `load_image()` возвращает список → обработать каждое изображение
- Одна страница — вернуть `AnalyzeResponse`
- Несколько страниц — вернуть `{"pages": [...]}`

**`app/static/index.html`** — обновить:
- `accept="image/*,.pdf,.svg"` в input
- Текст описания: «PNG/JPG/WEBP/PDF/SVG»

---

## ~~Часть E — Антигаллюцинации и качество распознавания~~ DONE

### E1. [CRITICAL] `app/model.py` — убран ненадёжный primary endpoint LM Studio
- Удалён нестандартный эндпоинт `/api/v1/chat` с кастомным форматом `input`/`data_url`
- Оставлен только OpenAI-совместимый `/v1/chat/completions` с `image_url`
- Причина: primary endpoint мог возвращать 200 без обработки изображения → модель галлюцинировала

### E2. [CRITICAL] `app/model.py` — промпт из `system` в `user` message
- Vision-модели лучше следуют инструкциям когда они в одном сообщении с картинкой

### E3. [HIGH] `app/prompt.py` — усиление промпта
- Промпт переведён на русский для лучшей работы с русскоязычными диаграммами
- Добавлены явные инструкции: «НЕ ПРИДУМЫВАЙ», «ДОСЛОВНАЯ копия текста», «нечитаемо» вместо фантазий

### E4. [HIGH] `app/preprocess.py` — извлечение текста из SVG
- Новая функция `extract_svg_texts()` — парсит `<text>`/`<tspan>` элементы из SVG XML
- Извлечённый текст передаётся модели как «точный справочник» в промпте
- `load_image()` теперь возвращает `tuple[list[Image], str | None]`

### E5. [HIGH] `app/preprocess.py` — увеличен масштаб рендера SVG
- `cairosvg.svg2png(scale=2)` — текст на картинке вдвое крупнее до ресайза к 1024px

### E6. [MEDIUM] `app/config.py` — temperature снижен до 0
- Убрана случайность, модель выбирает наиболее вероятные токены

### E7. [MEDIUM] `app/main.py`, `app/model.py` — прокидывание extracted_text
- `load_image()` → `(images, extracted_text)` → `model.infer(..., extracted_text=...)` → дополнение промпта

### E8. [LOW] `app/static/index.html` — фикс кнопки «Остановить»
- `display: 'inline-block'` вместо `''` — CSS `#stop-btn { display: none }` больше не перекрывает inline-стиль

### E9. [LOW] `app/static/index.html` — превью для PDF
- Сервер возвращает `preview` (base64 PNG) в ответе
- UI подхватывает `preview` если клиентского превью нет (PDF файлы)
- Для не-image файлов `readAsDataURL` не вызывается

---

## Порядок выполнения

| Шаг | Часть | Файлы | Описание | Статус |
|-----|-------|-------|----------|--------|
| 1 | A1 | `app/main.py` | `model_name` → `model_id` | DONE |
| 2 | A2 | `app/config.py` | Убрать неявное LM Studio | DONE |
| 3 | A3 | `.gitignore` | `.env` + дубликат | DONE |
| 4 | A5 | `app/postprocess.py` | Regex fix | DONE |
| 5 | A6 | `app/prompt.py` | `id` → `step` | DONE |
| 6 | A9 | `Examples/46..json` | Переименовать | DONE |
| 7 | D | `app/preprocess.py` | PDF/SVG + фикс формата (A7) | DONE |
| 8 | D | `app/main.py` | Многостраничная обработка | DONE |
| 9 | B+A4 | `app/static/index.html` | Остановить + XSS fix + форматы | DONE |
| 10 | C | `app/model.py` | Условный импорт torch | DONE |
| 11 | C | `requirements.txt`, `requirements-gpu.txt` | Два файла зависимостей | DONE |
| 12 | C | `Dockerfile`, `Dockerfile.gpu` | Два Dockerfile | DONE |
| 13 | C | `docker-compose.yml`, `docker-compose.gpu.yml` | Два compose | DONE |
| 14 | E1+E2 | `app/model.py` | Убран primary endpoint, промпт в user message | DONE |
| 15 | E3 | `app/prompt.py` | Усиление промпта, антигаллюцинации | DONE |
| 16 | E4+E5 | `app/preprocess.py` | Извлечение текста из SVG, scale=2 | DONE |
| 17 | E6 | `app/config.py` | temperature=0 | DONE |
| 18 | E7 | `app/main.py`, `app/model.py` | Прокидывание extracted_text | DONE |
| 19 | E8 | `app/static/index.html` | Фикс кнопки «Остановить» | DONE |
| 20 | E9 | `app/static/index.html`, `app/main.py` | Превью для PDF | DONE |
