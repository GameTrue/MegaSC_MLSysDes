import json
import re
from typing import Any, Dict, List, Union

from app.schemas import AnalyzeResponse, Step


def extract_json(text: str) -> Dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = text[start : end + 1]
    for cand in [snippet, re.sub(r'(?<=[{,\[])\s*(\w+)\s*:', r' "\1":', snippet)]:
        try:
            return json.loads(cand)
        except Exception:
            continue
    return {}


def normalize_action(action: str) -> str:
    """Trim whitespace and allow truly empty node text."""
    action = (action or "").strip()
    return " ".join(action.split())


def to_response(raw_text: str) -> AnalyzeResponse:
    # Try to parse diagram_type from leading line "diagram_type: xxx"
    diagram_type = "unknown"
    stripped = raw_text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if first_line.lower().startswith("diagram_type"):
        parts = first_line.split(":", 1)
        if len(parts) == 2:
            diagram_type = parts[1].strip()
            stripped = "\n".join(stripped.splitlines()[1:]).strip()

    # Also allow JSON payloads that might contain table
    payload = extract_json(raw_text)
    if payload and payload.get("diagram_type") and payload.get("table"):
        diagram_type = payload.get("diagram_type", diagram_type)
        table = payload.get("table", "").strip()
    else:
        table = stripped

    return AnalyzeResponse(diagram_type=diagram_type, table=table, raw=raw_text, steps=None)
