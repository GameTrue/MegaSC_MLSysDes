# Скрипт демо
- Запустить сервис: `docker compose up --build` или `uvicorn app.main:app --reload`.
- Открыть Swagger: `http://localhost:8000/docs`, отправить `POST /api/analyze` с одним из файлов из `Диаграммы. 2 часть/Диаграммы. 2 часть/Picture`.
- Показать ответ JSON и сопоставить со схемой.
- Открыть Gradio: `http://localhost:7860`, загрузить 2–3 изображения подряд, отметить стабильность ролей и шагов.
- Показать `scripts/benchmark.py --dir <папка> --limit 5` и вывести среднюю латентность.
- Завершить выводом системных метрик: `nvidia-smi` (VRAM < 8GB), время на картинку ≤ 15 c.
- Для быстрого сухого прогона без скачивания модели: `USE_DUMMY=1 PYTHONPATH=. python scripts/benchmark.py --dir "<папка>" --limit 2`.
- Для удалённой модели в LM Studio: выставить `USE_LMSTUDIO=1 LMSTUDIO_BASE_URL=http://192.168.8.152:22227 LMSTUDIO_TOKEN=<token>` и прогнать те же шаги.
