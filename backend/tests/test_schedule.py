"""Schedule-table parsing: door/window rows become assets with a clear width
parsed from the SIZE column; materials/fixture schedules and size-less tables
yield nothing. Tests the pure row logic — no PyMuPDF table detection needed."""

from __future__ import annotations

from planlint.ingest.schedule import _assets_from_rows
from planlint.models import AssetType, Parameter

_BBOX = (0.0, 0.0, 100.0, 100.0)


def _widths(assets):
    return {a.label: a.measurements[Parameter.CLEAR_WIDTH] for a in assets}


def test_door_schedule_rows_yield_door_widths():
    rows = [
        ["MARK", "TYPE", "SIZE", "NOTES"],
        ["D1", "EXTERIOR DOOR", "3'-0\" x 6'-8\"", ""],
        ["D2", "INTERIOR DOOR", "2'-8\" x 6'-8\"", ""],
    ]
    assets = _assets_from_rows(rows, _BBOX)
    assert [a.type for a in assets] == [AssetType.DOOR, AssetType.DOOR]
    # The MARK column is the label (the canonical opening identifier).
    assert _widths(assets) == {"D1": 36.0, "D2": 32.0}
    assert all(a.source == "vlm-only" and a.confidence == 0.9 for a in assets)


def test_window_rows_detected_by_mark():
    # Rows carry only a W# mark, no literal 'window' word.
    rows = [["MARK", "SIZE"], ["W1", "2'-0\" x 3'-0\""], ["W2", "36\""]]
    assets = _assets_from_rows(rows, _BBOX)
    assert [a.type for a in assets] == [AssetType.WINDOW, AssetType.WINDOW]
    assert [a.measurements[Parameter.CLEAR_WIDTH] for a in assets] == [24.0, 36.0]


def test_materials_schedule_yields_nothing():
    # A real materials schedule (this sample's p25): a SIZE column, but the
    # items are bricks and molding, not openings.
    rows = [
        ["MARK", "ITEM", "SIZE", "NOTES"],
        ["F-00", "BRICK - SMOOTH", "6\"", ""],
        ["F-20", "FLOOR DECKING", "1x4", ""],
    ]
    assert _assets_from_rows(rows, _BBOX) == []


def test_table_without_size_column_is_ignored():
    rows = [["MARK", "ITEM", "NOTES"], ["D1", "DOOR", "paint white"]]
    assert _assets_from_rows(rows, _BBOX) == []


def test_door_row_without_a_parseable_size_is_skipped():
    rows = [["MARK", "TYPE", "SIZE"], ["D1", "DOOR", "SEE PLAN"]]
    assert _assets_from_rows(rows, _BBOX) == []
