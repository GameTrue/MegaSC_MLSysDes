"""
Оценка качества распознавания диаграмм на тестовом наборе.

Использование:
    python -m scripts.evaluate \
        --test-dir "path/to/Диаграммы. 2 часть/test" \
        --out eval_report.json \
        --api-url http://localhost:8000/api/analyze

Формат test.txt (ground truth):
    3.png
    Шаг                        | Роль
    1. Создание запроса        | Инициатор
    2. Оценка по критериям     | Координатор
    ...

Метрики:
    - node_count       — кол-во шагов (pred vs gt)
    - node_recall      — min(pred, gt) / gt
    - action_sim       — fuzzy-match текста шагов (лучшее соответствие)
    - role_accuracy    — доля совпавших ролей
    - latency_s        — время обработки
"""

import argparse
import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import httpx


# ---------------------------------------------------------------------------
# Парсинг test.txt
# ---------------------------------------------------------------------------

def parse_test_txt(test_txt: Path) -> dict[str, list[dict]]:
    """
    Парсит test.txt → {filename: [{"action": ..., "role": ...}, ...]}
    """
    text = test_txt.read_text(encoding="utf-8")
    blocks: dict[str, list[dict]] = {}
    current_file = None
    current_steps = []

    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue

        # Имя файла: строка вида "3.png"
        if re.match(r"^\d+\.png$", line.strip()):
            if current_file and current_steps:
                blocks[current_file] = current_steps
            current_file = line.strip()
            current_steps = []
            continue

        # Пропуск заголовков "Шаг | Роль" или "Шаг"
        stripped = line.strip()
        if stripped.startswith("Шаг"):
            continue

        # Шаг: "1. Текст действия    | Роль" или "1. Текст действия"
        m = re.match(r"^\d+\s*[\.\)]\s*(.+)", stripped)
        if not m:
            continue

        rest = m.group(1).strip()
        if "|" in rest:
            parts = rest.rsplit("|", 1)
            action = parts[0].strip()
            role = parts[1].strip() if len(parts) > 1 else None
        else:
            action = rest
            role = None

        current_steps.append({"action": action, "role": role})

    if current_file and current_steps:
        blocks[current_file] = current_steps

    return blocks


# ---------------------------------------------------------------------------
# Сравнение
# ---------------------------------------------------------------------------

def sim(a: str, b: str) -> float:
    """Similarity двух строк (0..1)."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def best_match_sim(pred_actions: list[str], gt_actions: list[str]) -> float:
    """
    Для каждого gt-шага находит лучшее совпадение среди pred-шагов.
    Возвращает среднюю похожесть.
    """
    if not gt_actions:
        return 1.0 if not pred_actions else 0.0

    scores = []
    for ga in gt_actions:
        best = max((sim(pa, ga) for pa in pred_actions), default=0.0) if pred_actions else 0.0
        scores.append(best)
    return sum(scores) / len(scores)


def role_accuracy(pred_steps: list[dict], gt_steps: list[dict]) -> float:
    """
    Для каждого gt-шага с ролью находит лучший pred-шаг (по тексту)
    и сравнивает роль.
    """
    gt_with_role = [(s["action"], s["role"]) for s in gt_steps if s.get("role")]
    if not gt_with_role:
        return -1.0  # нет ролей в эталоне — метрика неприменима

    pred_list = [(s.get("action", ""), s.get("role")) for s in pred_steps]
    matches = 0
    for ga, gr in gt_with_role:
        # Найти ближайший pred по тексту
        best_pred = max(pred_list, key=lambda p: sim(p[0], ga), default=("", None))
        if best_pred[1] and gr:
            if sim(best_pred[1], gr) > 0.6:
                matches += 1
    return matches / len(gt_with_role)


def evaluate_single(pred: dict, gt_steps: list[dict]) -> dict:
    pred_steps = pred.get("steps", [])
    n_pred = len(pred_steps)
    n_gt = len(gt_steps)

    # Node metrics
    node_recall = min(n_pred, n_gt) / max(n_gt, 1)
    node_precision = min(n_pred, n_gt) / max(n_pred, 1)

    # Action similarity (best-match, order-independent)
    pred_actions = [s.get("action", "") for s in pred_steps]
    gt_actions = [s["action"] for s in gt_steps]
    action_similarity = best_match_sim(pred_actions, gt_actions)

    # Role accuracy
    role_acc = role_accuracy(pred_steps, gt_steps)

    return {
        "pred_nodes": n_pred,
        "gt_nodes": n_gt,
        "node_precision": round(node_precision, 3),
        "node_recall": round(node_recall, 3),
        "action_similarity": round(action_similarity, 3),
        "role_accuracy": round(role_acc, 3) if role_acc >= 0 else "n/a",
    }


# ---------------------------------------------------------------------------
# Основной прогон
# ---------------------------------------------------------------------------

def run_evaluation(test_dir: Path, api_url: str) -> dict:
    test_txt = test_dir / "test.txt"
    if not test_txt.exists():
        raise FileNotFoundError(f"test.txt not found in {test_dir}")

    ground_truth = parse_test_txt(test_txt)
    print(f"Ground truth: {len(ground_truth)} файлов\n")

    results = []
    total_latency = 0.0

    for filename, gt_steps in sorted(ground_truth.items()):
        img_path = test_dir / filename
        if not img_path.exists():
            print(f"  SKIP {filename} — файл не найден")
            results.append({"file": filename, "error": "file not found"})
            continue

        # Отправка на API
        start = time.time()
        try:
            with httpx.Client(timeout=300, trust_env=False) as client, open(img_path, "rb") as f:
                resp = client.post(api_url, files={"file": (filename, f)})
            latency = time.time() - start
        except httpx.ConnectError:
            print(f"  ERROR {filename}: сервер недоступен ({api_url})")
            results.append({"file": filename, "error": "connection refused"})
            continue
        except Exception as e:
            print(f"  ERROR {filename}: {e}")
            results.append({"file": filename, "error": str(e)})
            continue

        if not resp.is_success:
            print(f"  ERROR {filename}: HTTP {resp.status_code} — {resp.text[:200]}")
            results.append({"file": filename, "error": f"HTTP {resp.status_code}"})
            continue

        try:
            pred = resp.json()
        except Exception:
            print(f"  ERROR {filename}: ответ не JSON — {resp.text[:200]}")
            results.append({"file": filename, "error": "invalid JSON response"})
            continue

        total_latency += latency
        metrics = evaluate_single(pred, gt_steps)
        metrics["file"] = filename
        metrics["latency_s"] = round(latency, 2)
        results.append(metrics)

        role_str = f"role_acc={metrics['role_accuracy']}" if metrics['role_accuracy'] != "n/a" else "roles=n/a"
        print(f"  {filename}: nodes={metrics['pred_nodes']}/{metrics['gt_nodes']} "
              f"action_sim={metrics['action_similarity']:.2f} {role_str} "
              f"latency={latency:.1f}s")

    # Агрегация
    valid = [r for r in results if "error" not in r]
    n = max(len(valid), 1)
    latencies = sorted(r["latency_s"] for r in valid)

    role_valid = [r for r in valid if r.get("role_accuracy") != "n/a"]
    n_role = max(len(role_valid), 1)

    summary = {
        "total_files": len(ground_truth),
        "evaluated": len(valid),
        "errors": sum(1 for r in results if "error" in r),
        "avg_node_precision": round(sum(r["node_precision"] for r in valid) / n, 3),
        "avg_node_recall": round(sum(r["node_recall"] for r in valid) / n, 3),
        "avg_action_similarity": round(sum(r["action_similarity"] for r in valid) / n, 3),
        "avg_role_accuracy": round(sum(r["role_accuracy"] for r in role_valid) / n_role, 3) if role_valid else "n/a",
        "avg_latency_s": round(total_latency / n, 2),
        "p50_latency_s": round(latencies[len(latencies) // 2], 2) if latencies else 0,
        "p95_latency_s": round(latencies[int(len(latencies) * 0.95)], 2) if latencies else 0,
    }

    return {"summary": summary, "details": results}


# ---------------------------------------------------------------------------
# Табличный вывод
# ---------------------------------------------------------------------------

def print_table(report: dict):
    details = report["details"]
    summary = report["summary"]

    valid = [r for r in details if "error" not in r]
    errors = [r for r in details if "error" in r]

    # Заголовок
    header = f"{'Файл':<12} {'Узлы':>10} {'Node P':>8} {'Node R':>8} {'Action':>8} {'Роли':>8} {'Время':>8}"
    sep = "─" * len(header)

    print(f"\n{sep}")
    print("  РЕЗУЛЬТАТЫ ОЦЕНКИ КАЧЕСТВА")
    print(sep)
    print(header)
    print(sep)

    for r in sorted(valid, key=lambda x: x["file"]):
        nodes = f"{r['pred_nodes']}/{r['gt_nodes']}"
        role = f"{r['role_accuracy']:.2f}" if r["role_accuracy"] != "n/a" else "  —"
        print(f"{r['file']:<12} {nodes:>10} {r['node_precision']:>8.2f} {r['node_recall']:>8.2f} "
              f"{r['action_similarity']:>8.2f} {role:>8} {r['latency_s']:>7.1f}s")

    if errors:
        print(sep)
        for r in errors:
            print(f"{r['file']:<12} {'ОШИБКА':>10}   {r['error']}")

    print(sep)

    # Итоги
    s = summary
    role_avg = f"{s['avg_role_accuracy']:.2f}" if s["avg_role_accuracy"] != "n/a" else "  —"
    print(f"{'СРЕДНЕЕ':<12} {'':>10} {s['avg_node_precision']:>8.2f} {s['avg_node_recall']:>8.2f} "
          f"{s['avg_action_similarity']:>8.2f} {role_avg:>8} {s['avg_latency_s']:>7.1f}s")
    print(sep)

    print(f"\n  Файлов:     {s['total_files']}")
    print(f"  Оценено:    {s['evaluated']}")
    print(f"  Ошибок:     {s['errors']}")
    print(f"  Latency p50: {s['p50_latency_s']}s")
    print(f"  Latency p95: {s['p95_latency_s']}s")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Оценка качества распознавания диаграмм")
    parser.add_argument("--test-dir", type=Path, required=True,
                        help="Директория с тестовыми изображениями и test.txt")
    parser.add_argument("--out", type=Path, default=Path("eval_report.json"),
                        help="Путь для сохранения отчёта")
    parser.add_argument("--api-url", type=str, default="http://localhost:8000/api/analyze",
                        help="URL эндпоинта API")
    parser.add_argument("--format", choices=["json", "table"], default="table",
                        help="Формат вывода: json или table (по умолчанию table)")
    args = parser.parse_args()

    print(f"Тестовая директория: {args.test_dir}")
    print(f"API: {args.api_url}\n")

    report = run_evaluation(args.test_dir, args.api_url)

    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nОтчёт сохранён: {args.out}")

    if args.format == "table":
        print_table(report)
    else:
        print(f"\n{'='*50}")
        print("ИТОГИ")
        print(f"{'='*50}")
        for k, v in report["summary"].items():
            print(f"  {k}: {v}")
