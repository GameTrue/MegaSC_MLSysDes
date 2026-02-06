"""Programmatic extraction of draw.io graph from SVG files with mxGraphModel metadata."""

import base64
import re
import xml.etree.ElementTree as ET
import zlib
from typing import Optional
from urllib.parse import unquote

from app.schemas import AnalyzeResponse, Step


def is_drawio_svg(file_bytes: bytes) -> bool:
    """Detect whether an SVG was exported from draw.io / diagrams.net."""
    header = file_bytes[:2000].lower()
    return (
        b"mxgraphmodel" in header
        or b"mxfile" in header
        or b"draw.io" in header
        or b"diagrams.net" in header
    )


def _decode_diagram(encoded: str) -> str:
    """Decode draw.io encoded diagram content: base64 → deflate-decompress → URL-decode."""
    raw = base64.b64decode(encoded)
    # draw.io uses raw deflate (wbits=-15) or zlib (wbits=15); try both
    try:
        xml_bytes = zlib.decompress(raw, -15)
    except zlib.error:
        try:
            xml_bytes = zlib.decompress(raw)
        except zlib.error:
            xml_bytes = raw
    return unquote(xml_bytes.decode("utf-8", errors="replace"))


def _find_mxgraph_model(file_bytes: bytes) -> Optional[ET.Element]:
    """Find <mxGraphModel> element using multiple strategies."""
    text = file_bytes.decode("utf-8", errors="replace")

    # Strategy 1: direct <mxGraphModel> in XML
    m = re.search(r"(<mxGraphModel[\s>].*?</mxGraphModel>)", text, re.DOTALL)
    if m:
        try:
            return ET.fromstring(m.group(1))
        except ET.ParseError:
            pass

    # Strategy 2: <diagram ...>encoded</diagram>
    m = re.search(r"<diagram[^>]*>(.*?)</diagram>", text, re.DOTALL)
    if m:
        encoded = m.group(1).strip()
        if encoded:
            try:
                decoded = _decode_diagram(encoded)
                return ET.fromstring(decoded)
            except (ET.ParseError, Exception):
                pass

    # Strategy 3: content="..." attribute on root SVG element (HTML-encoded)
    try:
        root = ET.fromstring(file_bytes)
        content_attr = root.get("content", "")
        if not content_attr:
            # Also check for a <div> wrapper with data
            for elem in root.iter():
                content_attr = elem.get("content", "")
                if "mxGraphModel" in content_attr:
                    break
        if "mxGraphModel" in content_attr:
            return ET.fromstring(content_attr)
    except (ET.ParseError, Exception):
        pass

    return None


def _get_cell_label(cell: ET.Element) -> str:
    """Extract label text from mxCell, stripping HTML tags if present."""
    value = cell.get("value", "")
    if not value:
        return ""
    # Strip HTML tags (draw.io often wraps labels in <div>, <b>, <br>, etc.)
    clean = re.sub(r"<[^>]+>", " ", value)
    clean = re.sub(r"&nbsp;", " ", clean)
    clean = re.sub(r"&amp;", "&", clean)
    clean = re.sub(r"&lt;", "<", clean)
    clean = re.sub(r"&gt;", ">", clean)
    clean = re.sub(r"&quot;", '"', clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _parse_style(style: str) -> dict[str, str]:
    """Parse draw.io style string 'key1=val1;key2=val2;bare;' into dict."""
    result: dict[str, str] = {}
    if not style:
        return result
    for part in style.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
        else:
            result[part] = ""
    return result


def _classify_node(style_str: str) -> str:
    """Classify a vertex node type based on its draw.io style."""
    s = style_str.lower()
    style = _parse_style(s)

    if "swimlane" in style:
        return "swimlane"

    shape = style.get("shape", "")

    # Ellipse / terminator → start or end (resolved later by connectivity)
    if "ellipse" in style or shape in (
        "mxgraph.flowchart.terminator",
        "mxgraph.bpmn.shape",
    ):
        return "startend"

    # Diamond / decision
    if "rhombus" in style or shape in (
        "mxgraph.flowchart.decision",
    ):
        return "decision"

    return "task"


def extract_drawio_svg(file_bytes: bytes) -> Optional[AnalyzeResponse]:
    """Extract graph structure from a draw.io SVG. Returns None if not draw.io or parse fails."""
    if not is_drawio_svg(file_bytes):
        return None

    model = _find_mxgraph_model(file_bytes)
    if model is None:
        return None

    # Collect all mxCell elements
    cells: dict[str, ET.Element] = {}
    for cell in model.iter("mxCell"):
        cid = cell.get("id")
        if cid:
            cells[cid] = cell

    # Also check for <UserObject> / <object> wrappers (draw.io sometimes wraps cells)
    for wrapper_tag in ("UserObject", "object"):
        for wrapper in model.iter(wrapper_tag):
            inner = wrapper.find("mxCell")
            if inner is not None:
                cid = wrapper.get("id")
                if cid:
                    # Merge label from wrapper to inner cell
                    if not inner.get("value"):
                        inner.set("value", wrapper.get("label", wrapper.get("value", "")))
                    inner.set("id", cid)
                    cells[cid] = inner

    # Classify cells
    swimlanes: dict[str, str] = {}  # id → name
    nodes: dict[str, dict] = {}     # id → {label, type, parent}
    edges: list[dict] = []          # [{source, target, label}]

    for cid, cell in cells.items():
        style_str = cell.get("style", "")
        parent = cell.get("parent", "")

        if cell.get("vertex") == "1":
            label = _get_cell_label(cell)
            node_type = _classify_node(style_str)

            if node_type == "swimlane":
                swimlanes[cid] = label
            else:
                nodes[cid] = {
                    "label": label,
                    "type": node_type,
                    "parent": parent,
                }

        elif cell.get("edge") == "1":
            source = cell.get("source", "")
            target = cell.get("target", "")
            label = _get_cell_label(cell)
            if source and target:
                edges.append({
                    "source": source,
                    "target": target,
                    "label": label,
                })

    if not nodes:
        return None

    # Build incoming/outgoing sets for start/end classification
    incoming: dict[str, int] = {nid: 0 for nid in nodes}
    outgoing: dict[str, int] = {nid: 0 for nid in nodes}
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if tgt in incoming:
            incoming[tgt] += 1
        if src in outgoing:
            outgoing[src] += 1

    # Resolve startend → start or end
    for nid, node in nodes.items():
        if node["type"] == "startend":
            has_in = incoming.get(nid, 0) > 0
            has_out = outgoing.get(nid, 0) > 0
            if has_in and not has_out:
                node["type"] = "end"
            elif has_out and not has_in:
                node["type"] = "start"
            elif not has_in and not has_out:
                # Isolated ellipse — default to start
                node["type"] = "start"
            else:
                # Both in and out — treat as task (pass-through)
                node["type"] = "task"

    # Build edge map: source_id → [(target_id, label)]
    edge_map: dict[str, list[tuple[str, str]]] = {}
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in nodes and tgt in nodes:
            edge_map.setdefault(src, []).append((tgt, edge["label"]))

    # Assign stable step IDs
    id_map: dict[str, str | int] = {}
    counter = 1
    start_count = 0
    end_count = 0
    for nid, node in nodes.items():
        if node["type"] == "start":
            start_count += 1
            id_map[nid] = "start" if start_count == 1 else f"start_{start_count}"
        elif node["type"] == "end":
            end_count += 1
            id_map[nid] = "end" if end_count == 1 else f"end_{end_count}"
        else:
            id_map[nid] = counter
            counter += 1

    # Build steps
    steps: list[Step] = []
    for nid, node in nodes.items():
        step_id = id_map[nid]
        next_steps = []
        for target_id, label in edge_map.get(nid, []):
            if target_id in id_map:
                next_steps.append({"to": id_map[target_id], "label": label})

        # Resolve lane from parent chain
        lane = None
        parent = node["parent"]
        if parent in swimlanes:
            lane = swimlanes[parent]
        elif parent in nodes and nodes[parent]["parent"] in swimlanes:
            lane = swimlanes[nodes[parent]["parent"]]

        steps.append(Step(
            step=step_id,
            action=node["label"],
            role=lane,
            type=node["type"],
            next_steps=next_steps,
        ))

    if not steps:
        return None

    lane_names = [name for name in swimlanes.values() if name]
    description = (
        f"Draw.io диаграмма: {', '.join(lane_names)}" if lane_names
        else "Draw.io диаграмма"
    )

    return AnalyzeResponse(
        diagram_type="bpmn",
        description=description,
        steps=steps,
    )
