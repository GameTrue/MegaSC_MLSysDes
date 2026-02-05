import argparse
import time
from pathlib import Path
import json
from PIL import Image

from app import model
from app.prompt import PROMPT_TEMPLATE


def bench(images_dir: Path, limit: int | None):
    files = [p for p in images_dir.rglob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    if limit:
        files = files[:limit]
    results = []
    for path in files:
        image = Image.open(path).convert("RGB")
        start = time.time()
        text = model.infer(image, PROMPT_TEMPLATE)
        latency = time.time() - start
        results.append({"file": str(path), "latency_s": latency, "output": text})
        print(f"{path.name}: {latency:.2f}s")
    avg = sum(r["latency_s"] for r in results) / max(len(results), 1)
    return {"count": len(results), "avg_latency_s": avg, "items": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True, help="Folder with images")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=Path("benchmark.json"))
    args = parser.parse_args()

    report = bench(args.dir, args.limit)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {args.out}")
