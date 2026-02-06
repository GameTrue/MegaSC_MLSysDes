import json
import logging
import re
from typing import Any, Dict, List, Union

from app.schemas import AnalyzeResponse, Step

logger = logging.getLogger(__name__)


def _fix_json_escapes(text: str) -> str:
    """Fix invalid JSON escape sequences (e.g. \\G → \\\\G)."""
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)


def extract_json(text: str) -> Dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = text[start : end + 1]
    candidates = [
        snippet,
        _fix_json_escapes(snippet),
        re.sub(r'(?<=[{,\[])\s*(\w+)\s*:', r' "\1":', snippet),
        _fix_json_escapes(re.sub(r'(?<=[{,\[])\s*(\w+)\s*:', r' "\1":', snippet)),
    ]
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            continue
    logger.warning("Failed to extract JSON from model output (len=%d): %.200s", len(text), text)
    return {}


def normalize_action(action: str) -> str:
    """Trim whitespace and allow truly empty node text."""
    action = (action or "").strip()
    return " ".join(action.split())


def to_response(raw_text: str) -> AnalyzeResponse:
    payload = extract_json(raw_text)

    # sometimes the whole JSON is embedded as a string in description; try to parse it
    if isinstance(payload.get("description"), str):
        inner_desc = payload["description"]
        if ("steps" in inner_desc) and ("{" in inner_desc) and ("}" in inner_desc):
            inner = extract_json(inner_desc)
            if inner:
                payload = inner

    diagram_type = payload.get("diagram_type") or payload.get("type") or "unknown"
    description = payload.get("description") or raw_text.strip()

    raw_steps = payload.get("steps") or []
    steps: List[Step] = []
    for idx, item in enumerate(raw_steps, start=1):
        if isinstance(item, dict):
            step_id = item.get("id") or item.get("step") or idx
            action = normalize_action(str(item.get("action") or item.get("text") or ""))
            shape_type = item.get("type")
            role = item.get("role")
            next_steps = item.get("next_steps") or []
        else:
            step_id = idx
            action = normalize_action(str(item))
            shape_type = None
            role = None
            next_steps = []
        cleaned_next = []
        for ns in next_steps:
            if isinstance(ns, dict):
                cleaned_next.append({"to": ns.get("to"), "label": ns.get("label", "")})
        steps.append(Step(step=step_id, action=action, role=role, type=shape_type, next_steps=cleaned_next))

    if not steps:
        steps.append(Step(step=1, action=description[:140] or "", role=None, type=None, next_steps=[]))

    # fallback linear chain only if absolutely no next_steps
    if all((not s.next_steps) for s in steps) and len(steps) > 1:
        for i in range(len(steps) - 1):
            steps[i].next_steps = [{"to": steps[i + 1].step, "label": ""}]

    return AnalyzeResponse(diagram_type=diagram_type, description=description, steps=steps)
