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


def parse_schedule(page) -> list[PhysicalAsset]:
    """Extract door/window widths from every schedule table PyMuPDF finds on a
    page. Empty when the page has no recoverable table with a size column."""
    assets: list[PhysicalAsset] = []
    for table in page.find_tables().tables:
        bbox = tuple(round(v, 1) for v in table.bbox)
        assets.extend(_assets_from_rows(table.extract(), bbox))
    return assets
