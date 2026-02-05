import json
import re
from typing import Any, Dict, List

from app.schemas import AnalyzeResponse, Step


def extract_json(text: str) -> Dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    snippet = match.group(0)
    try:
        return json.loads(snippet)
    except Exception:
        try:
            snippet = re.sub(r"(\w+):", r'"\1":', snippet)
            return json.loads(snippet)
        except Exception:
            return {}


def to_response(raw_text: str) -> AnalyzeResponse:
    payload = extract_json(raw_text)
    diagram_type = payload.get("diagram_type") or payload.get("type") or "unknown"
    description = payload.get("description") or raw_text.strip()
    steps_raw = payload.get("steps") or []
    steps: List[Step] = []
    for idx, item in enumerate(steps_raw, start=1):
        if isinstance(item, dict):
            step_num = int(item.get("step") or idx)
            action = str(item.get("action") or item.get("text") or "")
            role = item.get("role")
        else:
            step_num = idx
            action = str(item)
            role = None
        if action:
            steps.append(Step(step=step_num, action=action, role=role))
    if not steps:
        steps.append(Step(step=1, action=description[:140], role=None))
    return AnalyzeResponse(diagram_type=diagram_type, description=description, steps=steps)
