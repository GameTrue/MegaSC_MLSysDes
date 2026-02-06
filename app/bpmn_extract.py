"""Programmatic extraction of BPMN graph from bpmn-js generated SVG files."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from app.schemas import AnalyzeResponse, Step

# Duplicated from preprocess.py to avoid circular import
_RU_SHORT_WORDS = {
    "и", "в", "с", "к", "о", "у", "а", "я", "на", "не", "но", "по", "до", "за", "из", "от",
    "ли", "ни", "же", "бы", "во", "ко",
}


def _maybe_join_suffix(m: re.Match) -> str:
    """Join 'Подтверждени е' → 'Подтверждение' but keep 'операции и' as is."""
    left, right = m.group(1), m.group(2)
    if right.lower() in _RU_SHORT_WORDS:
        return m.group(0)
    return left + right

# Threshold to distinguish container sub-processes from regular tasks
_CONTAINER_MIN_DIM = 200
# Tolerance in pixels when matching flow endpoints to shapes
_MATCH_TOLERANCE = 25


def is_bpmn_js_svg(file_bytes: bytes) -> bool:
    return b"bpmn-js" in file_bytes[:500] or b"bpmn.io" in file_bytes[:500]


@dataclass
class _Shape:
    element_id: str
    kind: str  # "task", "start", "end", "decision", "data_store"
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    label: str = ""
    lane: str = ""


@dataclass
class _Flow:
    element_id: str
    start_x: float = 0.0
    start_y: float = 0.0
    end_x: float = 0.0
    end_y: float = 0.0
    label: str = ""


@dataclass
class _Lane:
    element_id: str
    name: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _get_transform_xy(g) -> tuple[float, float]:
    t = g.get("transform", "")
    m = re.search(r"matrix\([^)]*\s+([\d.]+)\s+([\d.]+)\)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    return 0.0, 0.0


def _get_text(g) -> str:
    """Collect all tspan text under a <g>, join and fix broken words."""
    # find the first <text> element
    text_elem = None
    for elem in g.iter():
        if _strip_ns(elem.tag) == "text":
            text_elem = elem
            break
    if text_elem is None:
        return ""
    parts = []
    if text_elem.text and text_elem.text.strip():
        parts.append(text_elem.text.strip())
    for child in text_elem:
        if _strip_ns(child.tag) == "tspan":
            t = (child.text or "").strip()
            if t:
                parts.append(t)
    if not parts:
        return ""
    raw = " ".join(parts)
    raw = re.sub(r"(\w{2,}) (\w{1,2})\b(?= |$)", _maybe_join_suffix, raw)
    return raw


def _get_rect(g) -> tuple[float, float]:
    """Return (width, height) of first <rect> found."""
    for elem in g.iter():
        if _strip_ns(elem.tag) == "rect":
            return float(elem.get("width", 0)), float(elem.get("height", 0))
    return 0.0, 0.0


def _get_circle_stroke_width(g) -> float:
    for elem in g.iter():
        if _strip_ns(elem.tag) == "circle":
            style = elem.get("style", "")
            m = re.search(r"stroke-width:\s*([\d.]+)", style)
            if m:
                return float(m.group(1))
            r = float(elem.get("r", 0))
            return r  # fallback
    return 0.0


def _get_circle_radius(g) -> float:
    for elem in g.iter():
        if _strip_ns(elem.tag) == "circle":
            return float(elem.get("r", 18))
    return 18.0


def _parse_path_endpoints(g) -> tuple[float, float, float, float]:
    """Extract start (M) and end coordinates from the first <path> d attribute."""
    for elem in g.iter():
        if _strip_ns(elem.tag) == "path":
            d = elem.get("d", "")
            if not d or "M" not in d:
                continue
            coords = re.findall(r"[\d.]+", d)
            if len(coords) >= 4:
                return (
                    float(coords[0]),
                    float(coords[1]),
                    float(coords[-2]),
                    float(coords[-1]),
                )
    return 0.0, 0.0, 0.0, 0.0


def _point_near_shape(px: float, py: float, shape: _Shape) -> bool:
    """Check if a point is near or inside a shape's bounding box."""
    t = _MATCH_TOLERANCE
    return (
        shape.x - t <= px <= shape.x + shape.width + t
        and shape.y - t <= py <= shape.y + shape.height + t
    )


def _find_shape_at(
    px: float, py: float, shapes: list[_Shape], exclude: Optional[_Shape] = None,
) -> Optional[_Shape]:
    """Find the closest shape to the given point, optionally excluding one."""
    best = None
    best_dist = float("inf")
    for s in shapes:
        if exclude and s.element_id == exclude.element_id:
            continue
        if _point_near_shape(px, py, s):
            cx = s.x + s.width / 2
            cy = s.y + s.height / 2
            dist = (px - cx) ** 2 + (py - cy) ** 2
            if dist < best_dist:
                best_dist = dist
                best = s
    return best


def extract_bpmn_svg(file_bytes: bytes) -> Optional[AnalyzeResponse]:
    """Extract BPMN structure from a bpmn-js SVG. Returns None if not bpmn-js."""
    if not is_bpmn_js_svg(file_bytes):
        return None

    try:
        root = ET.fromstring(file_bytes)
    except ET.ParseError:
        return None

    # Collect all <g> elements with data-element-id
    g_map: dict[str, any] = {}
    for g in root.iter():
        if _strip_ns(g.tag) != "g":
            continue
        eid = g.get("data-element-id")
        if eid:
            g_map[eid] = g

    # --- Parse lanes ---
    lanes: list[_Lane] = []
    for eid, g in g_map.items():
        if not eid.startswith("Participant_"):
            continue
        x, y = _get_transform_xy(g)
        w, h = _get_rect(g)
        name = _get_text(g)
        if w > 0 and h > 0:
            lanes.append(_Lane(element_id=eid, name=name, x=x, y=y, width=w, height=h))

    # --- Parse shapes ---
    shapes: list[_Shape] = []
    containers: list[_Shape] = []

    # Activities (tasks and containers)
    for eid, g in g_map.items():
        if not eid.startswith("Activity_") or "_label" in eid:
            continue
        x, y = _get_transform_xy(g)
        w, h = _get_rect(g)
        label = _get_text(g)
        label_eid = eid + "_label"
        if not label and label_eid in g_map:
            label = _get_text(g_map[label_eid])
        if w > _CONTAINER_MIN_DIM and h > _CONTAINER_MIN_DIM:
            containers.append(_Shape(
                element_id=eid, kind="subprocess", x=x, y=y, width=w, height=h, label=label,
            ))
        else:
            shapes.append(_Shape(
                element_id=eid, kind="task", x=x, y=y, width=w, height=h, label=label,
            ))

    # Events (start/end)
    for eid, g in g_map.items():
        if not eid.startswith("Event_") or "_label" in eid:
            continue
        x, y = _get_transform_xy(g)
        r = _get_circle_radius(g)
        sw = _get_circle_stroke_width(g)
        kind = "end" if sw >= 3 else "start"
        shapes.append(_Shape(
            element_id=eid, kind=kind, x=x, y=y, width=r * 2, height=r * 2, label="",
        ))

    # Gateways (decisions)
    for eid, g in g_map.items():
        if not eid.startswith("Gateway_") or "_label" in eid:
            continue
        x, y = _get_transform_xy(g)
        # Gateway diamond is 50x50 in bpmn-js
        label = _get_text(g)
        label_eid = eid + "_label"
        if not label and label_eid in g_map:
            label = _get_text(g_map[label_eid])
        shapes.append(_Shape(
            element_id=eid, kind="decision", x=x, y=y, width=50, height=50, label=label,
        ))

    # --- Assign lanes ---
    for shape in shapes:
        cx = shape.x + shape.width / 2
        cy = shape.y + shape.height / 2
        for lane in lanes:
            if (lane.x <= cx <= lane.x + lane.width and
                    lane.y <= cy <= lane.y + lane.height):
                shape.lane = lane.name
                break

    # --- Parse flows ---
    flows: list[_Flow] = []
    for eid, g in g_map.items():
        if not eid.startswith("Flow_"):
            continue
        sx, sy, ex, ey = _parse_path_endpoints(g)
        if sx == 0 and sy == 0:
            continue
        label = ""
        label_eid = eid + "_label"
        if label_eid in g_map:
            label = _get_text(g_map[label_eid])
        flows.append(_Flow(element_id=eid, start_x=sx, start_y=sy, end_x=ex, end_y=ey, label=label))

    # --- Match flows to shapes → build edges ---
    # Include containers in the search pool for matching boundary flows
    all_shapes = shapes + containers
    container_ids = {c.element_id for c in containers}

    def _children_of(container: _Shape) -> list[_Shape]:
        """Return shapes geometrically inside the container."""
        result = []
        for s in shapes:
            cx = s.x + s.width / 2
            cy = s.y + s.height / 2
            if (container.x <= cx <= container.x + container.width and
                    container.y <= cy <= container.y + container.height):
                result.append(s)
        return result

    container_children: dict[str, list[_Shape]] = {}
    if containers:
        for c in containers:
            container_children[c.element_id] = _children_of(c)

    def _resolve_container_entry(shape: _Shape, px: float, py: float) -> Optional[_Shape]:
        """Resolve a container to the nearest child (spatial — for incoming flows)."""
        if shape.element_id not in container_ids:
            return shape
        children = container_children.get(shape.element_id, [])
        if not children:
            return None
        best = None
        best_dist = float("inf")
        for child in children:
            cx = child.x + child.width / 2
            cy = child.y + child.height / 2
            dist = (px - cx) ** 2 + (py - cy) ** 2
            if dist < best_dist:
                best_dist = dist
                best = child
        return best

    def _resolve_container_exit(shape: _Shape, child_edges: dict) -> Optional[_Shape]:
        """Resolve a container to its exit child (graph-based — for outgoing flows).

        Exit child = end event, or child with no outgoing edges to siblings.
        """
        if shape.element_id not in container_ids:
            return shape
        children = container_children.get(shape.element_id, [])
        if not children:
            return None
        child_ids = {c.element_id for c in children}
        # Prefer end events
        end_events = [c for c in children if c.kind == "end"]
        if end_events:
            return end_events[0]
        # Fallback: child with no outgoing edges to other children
        exits = [c for c in children
                 if not any(t in child_ids for t, _ in child_edges.get(c.element_id, []))]
        return exits[0] if exits else children[-1]

    # First pass: build edges including containers
    edges: dict[str, list[tuple[str, str]]] = {}
    for flow in flows:
        src = _find_shape_at(flow.start_x, flow.start_y, all_shapes)
        if not src:
            continue
        dst = _find_shape_at(flow.end_x, flow.end_y, all_shapes, exclude=src)
        if not dst:
            dst = _find_shape_at(flow.end_x, flow.end_y, all_shapes)
        if not dst or src.element_id == dst.element_id:
            continue
        # Resolve container entries (dst is container)
        dst = _resolve_container_entry(dst, flow.end_x, flow.end_y)
        # For container exits, defer — need edge info first
        if src and dst and src.element_id != dst.element_id:
            edges.setdefault(src.element_id, []).append((dst.element_id, flow.label))

    # Second pass: resolve container exits using graph topology
    for cid in list(container_ids):
        if cid not in edges:
            continue
        container = next(c for c in containers if c.element_id == cid)
        exit_shape = _resolve_container_exit(container, edges)
        if exit_shape:
            outgoing = edges.pop(cid)
            for target_eid, label in outgoing:
                edges.setdefault(exit_shape.element_id, []).append((target_eid, label))

    # --- Build AnalyzeResponse ---
    # Create stable step IDs from labels or element_ids
    id_map: dict[str, str | int] = {}
    counter = 1
    start_count = 0
    end_count = 0
    for shape in shapes:
        if shape.kind == "start":
            start_count += 1
            id_map[shape.element_id] = "start" if start_count == 1 else f"start_{start_count}"
        elif shape.kind == "end":
            end_count += 1
            id_map[shape.element_id] = "end" if end_count == 1 else f"end_{end_count}"
        else:
            id_map[shape.element_id] = counter
            counter += 1

    steps: list[Step] = []
    for shape in shapes:
        step_id = id_map[shape.element_id]
        next_steps = []
        for target_eid, label in edges.get(shape.element_id, []):
            if target_eid in id_map:
                next_steps.append({"to": id_map[target_eid], "label": label})
        steps.append(Step(
            step=step_id,
            action=shape.label,
            role=shape.lane or None,
            type=shape.kind,
            next_steps=next_steps,
        ))

    if not steps:
        return None

    lane_names = [lane.name for lane in lanes if lane.name]
    description = f"BPMN-диаграмма: {', '.join(lane_names)}" if lane_names else "BPMN-диаграмма"

    return AnalyzeResponse(
        diagram_type="bpmn",
        description=description,
        steps=steps,
    )
