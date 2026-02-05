import json
import re
from typing import Any, Dict, List

from app.schemas import AnalyzeResponse, Step, Edge


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
            next_steps = item.get("next_steps") or []
        else:
            step_num = idx
            action = str(item)
            role = None
            next_steps = []
        # normalize action: strip service suffixes like "/текст", "/decision"
        if "/" in action:
            parts = action.split("/")
            # keep the first meaningful part as text, trailing part as shape
            text_part, *shape_parts = parts
            text_part = text_part.strip()
            shape = shape_parts[-1].strip() if shape_parts else ""
            # handle labels like "да" or "нет" accidentally merged into action
            if text_part.lower() in {"да", "нет"} and shape in {"decision", "решение"}:
                label_from_action = text_part
                text_part = "без текста"
                # push label into next_steps if missing
                for ns in next_steps or []:
                    if not ns.get("label"):
                        ns["label"] = label_from_action
            action = (text_part or "без текста") + (f"/{shape}" if shape else "")

        if action:
            steps.append(Step(step=step_num, action=action, role=role, next_steps=next_steps))
    if not steps:
        steps.append(Step(step=1, action=description[:140], role=None))

    edges_raw = payload.get("edges") or []
    edges: List[Edge] = []
    for item in edges_raw:
        if isinstance(item, dict):
            from_id = item.get("from") or item.get("from_id")
            to_id = item.get("to") or item.get("to_id")
            if from_id is None or to_id is None:
                continue
            label = item.get("label")
            edges.append(Edge(from_id=int(from_id), to_id=int(to_id), label=label))

    # if edges missing, assume linear flow
    if not edges and len(steps) > 1:
        for i in range(1, len(steps)):
            edges.append(Edge(from_id=i, to_id=i + 1, label=None))

    # merge edges into next_steps if model didn’t provide them
    edges_by_from: Dict[int, List[Dict[str, Any]]] = {}
    for e in edges:
        edges_by_from.setdefault(e.from_id, []).append({"to": e.to_id, "label": e.label})
    for s in steps:
        if not s.next_steps and s.step in edges_by_from:
            s.next_steps = edges_by_from[s.step]

    return AnalyzeResponse(diagram_type=diagram_type, description=description, steps=steps, edges=edges)
