"""Deterministic geometry extraction from PDF pages.

Everything here is pure math over PyMuPDF primitives — no LLM involvement.
The VLM only *classifies* entities; coordinates and measurements come from
the vector geometry whenever the PDF has any.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from planlint.models import BBox, Parameter

# How far (in PDF points) a VLM box is inflated when looking for vector
# geometry to snap to, and how far a dimension label may sit from an asset.
SNAP_INFLATE_PT = 8.0
LABEL_RADIUS_PT = 40.0
# Segments longer than this are structural (walls), not asset geometry —
# excluded from snapping so a door box never unions in a 30-ft wall.
MAX_SNAP_SEGMENT_PT = 100.0


@dataclass(frozen=True)
class Primitive:
    """A straight line segment in page coordinates (PDF points)."""

    p0: tuple[float, float]
    p1: tuple[float, float]

    @property
    def length(self) -> float:
        return ((self.p1[0] - self.p0[0]) ** 2 + (self.p1[1] - self.p0[1]) ** 2) ** 0.5

    @property
    def midpoint(self) -> tuple[float, float]:
        return ((self.p0[0] + self.p1[0]) / 2, (self.p0[1] + self.p1[1]) / 2)


@dataclass(frozen=True)
class TextLabel:
    text: str
    bbox: BBox


def detect_pdf_type(page) -> str:
    """A page with vector drawing commands is 'vector'; otherwise 'raster'."""
    return "vector" if page.get_drawings() else "raster"


def extract_primitives(page) -> tuple[list[Primitive], list[TextLabel]]:
    """Extract straight segments (lines + rectangle edges) and text labels."""
    primitives: list[Primitive] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            kind = item[0]
            if kind == "l":  # line: ("l", p0, p1)
                p0, p1 = item[1], item[2]
                primitives.append(Primitive(p0=(p0.x, p0.y), p1=(p1.x, p1.y)))
            elif kind == "re":  # rectangle: ("re", rect, ...)
                r = item[1]
                corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    primitives.append(Primitive(p0=a, p1=b))
            elif kind == "c":  # bezier: approximate by its chord
                p0, p3 = item[1], item[4]
                primitives.append(Primitive(p0=(p0.x, p0.y), p1=(p3.x, p3.y)))

    # Line-level text (blocks merge unrelated labels that share a baseline).
    labels: list[TextLabel] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                labels.append(TextLabel(text=text, bbox=(x0, y0, x1, y1)))
    return primitives, labels


# --------------------------------------------------------------------- scale

_SCALE_ARCH = re.compile(
    r"""(?P<num>\d+)\s*/\s*(?P<den>\d+)\s*"?\s*=\s*(?P<feet>\d+)\s*'(?:\s*-?\s*(?P<inches>\d+)\s*"?)?""",
    re.VERBOSE,
)
_SCALE_RATIO = re.compile(r"\b1\s*:\s*(?P<ratio>\d+)\b")


def parse_scale(text: str) -> float | None:
    """Parse a drawing-scale annotation into real-inches-per-PDF-point.

    '1/4" = 1'-0"'  ->  0.25 paper-in = 12 real-in  ->  48 real-in per paper-in
                    ->  48 / 72 real-in per PDF point.
    '1:50'          ->  50 / 72.
    """
    if not text:
        return None
    m = _SCALE_ARCH.search(text)
    if m:
        paper_in = int(m.group("num")) / int(m.group("den"))
        real_in = int(m.group("feet")) * 12 + int(m.group("inches") or 0)
        if paper_in > 0 and real_in > 0:
            return (real_in / paper_in) / 72.0
    m = _SCALE_RATIO.search(text)
    if m:
        ratio = int(m.group("ratio"))
        if ratio > 0:
            return ratio / 72.0
    return None


# --------------------------------------------------------------- dimensions

_DIM_FT_IN = re.compile(r"""(?P<feet>\d+)\s*'\s*-?\s*(?P<inches>\d+)\s*"?""")
_DIM_IN = re.compile(r"""(?P<inches>\d+(?:\.\d+)?)\s*(?:"|\bin\b\.?)""", re.IGNORECASE)


def parse_dimension_label(text: str) -> float | None:
    """Find a CAD dimension ('36"', "3'-0\"", '30 in', 'D1 36"') in a label."""
    text = text.strip()
    m = _DIM_FT_IN.search(text)
    if m:
        return int(m.group("feet")) * 12 + int(m.group("inches"))
    m = _DIM_IN.search(text)
    if m:
        return float(m.group("inches"))
    return None


# ------------------------------------------------------------------ snapping

def _inflate(box: BBox, by: float) -> BBox:
    x0, y0, x1, y1 = box
    return (x0 - by, y0 - by, x1 + by, y1 + by)


def _contains(box: BBox, point: tuple[float, float]) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= point[0] <= x1 and y0 <= point[1] <= y1


def snap_box(vlm_box: BBox, primitives: list[Primitive]) -> tuple[BBox, bool]:
    """Snap an approximate VLM box to the exact vector geometry beneath it.

    Returns (bbox, snapped). When no primitive's midpoint falls inside the
    inflated VLM box, the original box is returned unsnapped.
    """
    search = _inflate(vlm_box, SNAP_INFLATE_PT)
    candidates = [p for p in primitives if p.length <= MAX_SNAP_SEGMENT_PT]
    nearby = [p for p in candidates if _contains(search, p.midpoint)]
    if not nearby:
        return vlm_box, False

    # One hop of connectivity: pull in segments that share an endpoint with
    # the snapped set (a door's swing chord/arc connects to its leaf but its
    # midpoint often falls outside the label box).
    def touches(a: Primitive, b: Primitive, tol: float = 0.5) -> bool:
        return any(
            abs(pa[0] - pb[0]) <= tol and abs(pa[1] - pb[1]) <= tol
            for pa in (a.p0, a.p1)
            for pb in (b.p0, b.p1)
        )

    connected = [
        p
        for p in candidates
        if p not in nearby and any(touches(p, q) for q in nearby)
    ]
    cluster = nearby + connected
    xs = [c for p in cluster for c in (p.p0[0], p.p1[0])]
    ys = [c for p in cluster for c in (p.p0[1], p.p1[1])]
    return (min(xs), min(ys), max(xs), max(ys)), True


# --------------------------------------------------------------- measurement

def _box_distance(a: BBox, b: BBox) -> float:
    """Gap between two boxes (0 when they overlap)."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def measure_asset(
    asset_box: BBox,
    primitives: list[Primitive],
    labels: list[TextLabel],
    scale: float | None,
) -> tuple[dict[Parameter, float], bool] | None:
    """Derive real-world measurements for an asset.

    Priority: (1) a dimension label within LABEL_RADIUS_PT of the asset,
    (2) longest straight segment inside the box × scale. Returns
    (measurements, from_label) or None when nothing measurable exists.
    """
    best_label: tuple[float, float] | None = None  # (distance, inches)
    for label in labels:
        inches = parse_dimension_label(label.text)
        if inches is None:
            continue
        distance = _box_distance(asset_box, label.bbox)
        if distance <= LABEL_RADIUS_PT and (best_label is None or distance < best_label[0]):
            best_label = (distance, inches)
    if best_label is not None:
        return {Parameter.CLEAR_WIDTH: best_label[1]}, True

    if scale is not None:
        search = _inflate(asset_box, SNAP_INFLATE_PT)
        inside = [p for p in primitives if _contains(search, p.midpoint)]
        if inside:
            longest = max(p.length for p in inside)
            if longest > 0:
                return {Parameter.CLEAR_WIDTH: longest * scale}, False

    return None
