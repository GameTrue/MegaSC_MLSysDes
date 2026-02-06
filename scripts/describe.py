"""
Генерация текстовых описаний диаграмм из CSV результатов через LM Studio.

Использование:
    python -m scripts.describe \
        --csv бобы.csv \
        --images-dir "C:/Users/.../drive-download-..." \
        --out report.html \
        --api-url http://localhost:22227/v1/chat/completions
"""

import argparse
import base64
import csv
import html
import time
from collections import defaultdict
from pathlib import Path

import httpx

DESCRIBE_PROMPT = """Ниже дано структурированное описание диаграммы (тип, шаги, роли, связи).
Напиши связное текстовое описание алгоритма/процесса на русском языке.
Требования:
- Опиши процесс как последовательность действий с указанием ролей (если есть).
- Упомяни развилки (условия) и куда ведут ветви.
- Пиши от третьего лица, деловым стилем.
- Не используй markdown, JSON или таблицы — только чистый текст абзацами.
- Длина: 3–10 предложений в зависимости от сложности диаграммы.

Данные диаграммы:
Тип: {diagram_type}
Описание: {description}
Шаги:
{steps_text}
"""


def read_csv(csv_path: Path) -> dict:
    """Parse CSV → {filename: {type, description, steps: [...]}}."""
    diagrams = defaultdict(lambda: {"type": "", "description": "", "steps": []})
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            fname = row["Файл"]
            d = diagrams[fname]
            d["type"] = row["Тип диаграммы"]
            d["description"] = row["Описание"].strip('"')
            d["steps"].append({
                "id": row["№"],
                "action": row["Действие"],
                "type": row["Тип"],
                "role": row["Роль"] or None,
                "next": row["Следующие шаги"],
            })
    return dict(diagrams)


def steps_to_text(steps: list) -> str:
    lines = []
    for s in steps:
        role = f" [{s['role']}]" if s["role"] else ""
        next_s = f" → {s['next']}" if s["next"] else ""
        lines.append(f"  {s['id']}. {s['action']} (тип: {s['type']}){role}{next_s}")
    return "\n".join(lines)


def describe_via_lmstudio(diagram: dict, api_url: str, model: str, timeout: int) -> str:
    prompt = DESCRIBE_PROMPT.format(
        diagram_type=diagram["type"],
        description=diagram["description"],
        steps_text=steps_to_text(diagram["steps"]),
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        resp = client.post(api_url, json=payload, headers={"Content-Type": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    if "choices" in data and data["choices"]:
        return data["choices"][0].get("message", {}).get("content", "").strip()
    return "(ошибка генерации)"


def image_to_data_url(img_path: Path) -> str:
    suffix = img_path.suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "svg": "image/svg+xml", "webp": "image/webp"}.get(suffix.lstrip("."), "image/png")
    if suffix == ".pdf":
        return ""
    b64 = base64.b64encode(img_path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def build_html(diagrams: dict, descriptions: dict, images_dir: Path) -> str:
    parts = ["""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Описание диаграмм</title>
<style>
body { font-family: 'Segoe UI', sans-serif; max-width: 1000px; margin: 40px auto; padding: 0 20px; }
.diagram { margin-bottom: 50px; page-break-inside: avoid; }
h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 8px; }
.image-container { text-align: center; margin: 20px 0; }
.image-container img { max-width: 100%; max-height: 600px; border: 1px solid #ddd; border-radius: 4px; }
.description { background: #f8f9fa; padding: 20px; border-left: 4px solid #3498db; border-radius: 4px; line-height: 1.7; }
.meta { color: #666; font-size: 0.9em; margin-bottom: 10px; }
</style></head><body>
<h1>Текстовое описание диаграмм</h1>
"""]

    for fname in diagrams:
        desc = descriptions.get(fname, "(описание не сгенерировано)")
        dtype = diagrams[fname]["type"]
        img_path = images_dir / fname

        parts.append(f'<div class="diagram">')
        parts.append(f'<h2>{html.escape(fname)}</h2>')
        parts.append(f'<div class="meta">Тип: {html.escape(dtype)} | Шагов: {len(diagrams[fname]["steps"])}</div>')

        if img_path.exists() and img_path.suffix.lower() != ".pdf":
            data_url = image_to_data_url(img_path)
            if data_url:
                parts.append(f'<div class="image-container"><img src="{data_url}" alt="{html.escape(fname)}"></div>')
        elif img_path.exists() and img_path.suffix.lower() == ".pdf":
            parts.append(f'<div class="meta"><em>(PDF-файл — изображение не встроено)</em></div>')

        parts.append(f'<div class="description">{html.escape(desc)}</div>')
        parts.append('</div>')

    parts.append("</body></html>")
    return "\n".join(parts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Генерация текстовых описаний диаграмм")
    parser.add_argument("--csv", type=Path, required=True, help="CSV с результатами (бобы.csv)")
    parser.add_argument("--images-dir", type=Path, required=True, help="Директория с изображениями диаграмм")
    parser.add_argument("--out", type=Path, default=Path("report.html"), help="Выходной HTML файл")
    parser.add_argument("--api-url", type=str, default="http://localhost:22227/v1/chat/completions")
    parser.add_argument("--model", type=str, default="", help="ID модели (по умолчанию — из LM Studio)")
    parser.add_argument("--timeout", type=int, default=300, help="Таймаут запроса (сек)")
    args = parser.parse_args()

    print(f"CSV: {args.csv}")
    print(f"Изображения: {args.images_dir}")
    print(f"API: {args.api_url}\n")

    diagrams = read_csv(args.csv)
    print(f"Найдено {len(diagrams)} диаграмм\n")

    descriptions = {}
    for fname, data in diagrams.items():
        print(f"  {fname} ({len(data['steps'])} шагов)...", end=" ", flush=True)
        start = time.time()
        try:
            desc = describe_via_lmstudio(data, args.api_url, args.model, args.timeout)
            elapsed = time.time() - start
            descriptions[fname] = desc
            print(f"OK ({elapsed:.1f}s, {len(desc)} символов)")
        except Exception as e:
            descriptions[fname] = f"Ошибка генерации: {e}"
            print(f"ERROR: {e}")

    report = build_html(diagrams, descriptions, args.images_dir)
    args.out.write_text(report, encoding="utf-8")
    print(f"\nОтчёт сохранён: {args.out}")
