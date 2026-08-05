"""Schedule parsing: pull door/window clear widths straight from a schedule
table.

A well-formed door/window schedule lists each opening's size in a table; when
PyMuPDF recovers that table, the SIZE column gives a clear width with no vision
guessing — the highest-fidelity, lowest-risk width source available. Many CAD
sets, though, draw "schedules" as un-tabular line-art or carry only
materials/fixture schedules (no openings); we return nothing rather than guess.
The pure-Python checker still owns every verdict — this only supplies a measured
width, exactly like a snapped opening does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from planlint.ingest import vector_geometry as geometry
from planlint.models import AssetType, Parameter, PhysicalAsset

_HEADER_SIZE = re.compile(r"\b(SIZE|W\s*[x×]\s*H|WIDTH|DIMENSION)\b", re.IGNORECASE)
_HEADER_TYPE = re.compile(r"\b(TYPE|ITEM|DESCRIPTION|MARK|NAME)\b", re.IGNORECASE)
_DOOR = re.compile(r"\bDOOR\b", re.IGNORECASE)
_WINDOW = re.compile(r"\b(WINDOW|WDW|WIN)\b", re.IGNORECASE)
# A schedule mark like 'D1'/'D-01' (door) or 'W2' (window).
_MARK = re.compile(r"^(?P<kind>[DW])[-\s]?\d", re.IGNORECASE)


def _column(header: list[str], pattern: re.Pattern) -> int | None:
    for i, cell in enumerate(header):
        if cell and pattern.search(cell):
            return i
    return None


def _row_asset_type(cells: list[str]) -> AssetType | None:
    """Door vs window for one row, from an explicit word or a D#/W# mark."""
    joined = " ".join(c for c in cells if c)
    if _DOOR.search(joined):
        return AssetType.DOOR
    if _WINDOW.search(joined):
        return AssetType.WINDOW
    for cell in cells:
        mark = _MARK.match(cell.strip())
        if mark:
            return AssetType.DOOR if mark.group("kind").upper() == "D" else AssetType.WINDOW
    return None


def _assets_from_rows(rows: list[list[str]], bbox) -> list[PhysicalAsset]:
    """Door/window assets from one extracted table's rows. Split from
    `parse_schedule` so it is testable without PyMuPDF table detection."""
    if len(rows) < 2:
        return []
    header = [(c or "").strip() for c in rows[0]]
    size_col = _column(header, _HEADER_SIZE)
    if size_col is None:
        return []  # not a schedule with a size column — e.g. a materials list
    type_col = _column(header, _HEADER_TYPE)

    assets: list[PhysicalAsset] = []
    for raw in rows[1:]:
        cells = [(c or "").strip() for c in raw]
        asset_type = _row_asset_type(cells)
        if asset_type is None:
            continue
        if size_col >= len(cells):
            continue
        # SIZE is usually 'W x H'; parse_dimension_label picks the first (width).
        width_in = geometry.parse_dimension_label(cells[size_col])
        if width_in is None:
            continue
        label = cells[type_col] if type_col is not None and type_col < len(cells) else ""
        assets.append(
            PhysicalAsset(
                type=asset_type,
                label=label,
                bbox=bbox,
                confidence=0.9,  # a printed spec value, not a geometry guess
                source="vlm-only",  # off-drawing; no vector/raster snap applies
                measurements={Parameter.CLEAR_WIDTH: round(width_in, 1)},
            )
        )
    return assets


# ------------------------------------------------------- positioned-text index

@dataclass(frozen=True)
class OpeningSpec:
    """One door/window row from a schedule, keyed by its mark. `width_in` is the
    schedule's first size figure — the nominal opening size, recorded as the
    clear width when joined to a plan opening (a 36" door ≈ 34" clear, still far
    truer than a measured wall gap)."""

    kind: AssetType
    mark: str
    width_in: float
    height_in: float | None = None
    type_desc: str = ""


# A 'W x H' size token: two dimension figures joined by x/×. Loose char class so
# '36"x84"', "3'-0\"x6'-8\"", and '30" X 60"' all match.
_SIZE_TOKEN = re.compile(r"[\d'\"\-]+\s*[x×X]\s*[\d'\"\-]+")
_X_SPLIT = re.compile(r"\s*[x×X]\s*")
# A schedule mark cell: a short alphanumeric tag ('A', 'D1', '2').
_MARK_CELL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,3}$")


def parse_size(text: str) -> tuple[float, float | None] | None:
    """Parse a 'W x H' size ('36"x84"', "3'-0\"x6'-8\"") into (width_in, height_in).
    Height is None if the second figure is unreadable; None when there is no W×H."""
    m = _SIZE_TOKEN.search(text)
    if not m:
        return None
    parts = _X_SPLIT.split(m.group(0), maxsplit=1)
    if len(parts) != 2:
        return None
    width = geometry.parse_dimension_label(parts[0])
    if width is None:
        return None
    return (width, geometry.parse_dimension_label(parts[1]))


def _normalize_mark(text: str) -> str | None:
    """A schedule/callout mark reduced to its bare alphanumerics ('Ⓐ'→'A',
    'D-01'→'D01'), uppercased, so a plan callout and a schedule row match."""
    out: list[str] = []
    for ch in text.strip():
        o = ord(ch)
        if 0x2460 <= o <= 0x2468:  # ①..⑨
            out.append(str(o - 0x2460 + 1))
        elif 0x24B6 <= o <= 0x24CF:  # Ⓐ..
            out.append(chr(ord("A") + o - 0x24B6))
        elif ch.isalnum():
            out.append(ch.upper())
    return "".join(out) or None


def _group_lines(words: list, tol: float = 4.0) -> list[list]:
    """Cluster PyMuPDF words into visual lines by y, each sorted left→right.
    Schedule rows are ~20pt apart, so a small tolerance keeps rows distinct."""
    lines: list[tuple[float, list]] = []
    for w in sorted(words, key=lambda w: w[1]):
        if lines and abs(w[1] - lines[-1][0]) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append((w[1], [w]))
    return [sorted(ln, key=lambda w: w[0]) for _, ln in lines]


def parse_schedule_index(page) -> dict[str, OpeningSpec]:
    """Build a mark→OpeningSpec index from a schedule drawn as positioned text
    (many CAD schedules are line-art PyMuPDF `find_tables` can't recover). Door
    vs window comes from the nearest 'DOOR SCHEDULE' / 'WINDOW SCHEDULE' title
    above; each data row's mark is its leftmost token, size its 'W×H' figure."""
    index: dict[str, OpeningSpec] = {}
    kind: AssetType | None = None
    for line in _group_lines(page.get_text("words")):
        tokens = [w[4] for w in line]
        upper = " ".join(tokens).upper()
        if "SCHEDULE" in upper and len(tokens) <= 3:  # a short section title
            kind = (
                AssetType.DOOR if _DOOR.search(upper)
                else AssetType.WINDOW if _WINDOW.search(upper)
                else None  # materials/fixture schedule — not openings
            )
            continue
        if kind is None:
            continue
        # The SIZE cell is a single 'W×H' word; the MARK is the column just left
        # of it. Anchoring on the size (not the leftmost token) rejects vertical
        # margin text ('FREE', 'THESE …') that shares a row's y-band.
        size_word = next((w for w in line if _SIZE_TOKEN.search(w[4])), None)
        if size_word is None:
            continue
        size = parse_size(size_word[4])
        if size is None:
            continue
        mark: str | None = None
        for w in line:  # sorted left→right; keep the last valid mark before SIZE
            if w[0] < size_word[0] and _MARK_CELL.match(w[4]):
                mark = _normalize_mark(w[4]) or mark
        if mark is None:
            continue
        index.setdefault(
            mark, OpeningSpec(kind=kind, mark=mark, width_in=size[0], height_in=size[1])
        )
    return index


def parse_schedule(page) -> list[PhysicalAsset]:
    """Extract door/window widths from a page's schedule. Prefers a ruled table
    PyMuPDF recovers; falls back to the positioned-text index for schedules drawn
    as line-art. Empty when the page carries no opening schedule."""
    assets: list[PhysicalAsset] = []
    for table in page.find_tables().tables:
        bbox = tuple(round(v, 1) for v in table.bbox)
        assets.extend(_assets_from_rows(table.extract(), bbox))
    if assets:
        return assets
    page_bbox = tuple(round(v, 1) for v in page.rect)
    return [
        PhysicalAsset(
            type=spec.kind,
            label=spec.mark,
            bbox=page_bbox,
            confidence=0.9,  # a printed spec value, not a geometry guess
            source="schedule",
            measurements={Parameter.CLEAR_WIDTH: round(spec.width_in, 1)},
        )
        for spec in parse_schedule_index(page).values()
    ]
