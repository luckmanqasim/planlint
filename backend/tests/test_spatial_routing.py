"""Sheet-type routing in floor-plan ingestion: the plan-view detector runs only
on floor-plan pages, schedules are parsed for widths, and every other sheet is
recorded but not linted. No real vision calls."""

from __future__ import annotations

import pymupdf

from planlint.ingest import spatial
from planlint.ingest.sheet_type import SheetType
from planlint.ingest.vlm import VlmPage
from planlint.models import AssetType, Parameter, PhysicalAsset


def _blank_pdf(path, pages: int) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


async def test_only_floor_plans_are_detected_schedules_are_parsed(
    tmp_path, fake_repo, monkeypatch
):
    pdf = tmp_path / "set.pdf"
    _blank_pdf(pdf, pages=3)
    monkeypatch.setattr(spatial.settings, "planlint_fake_llm", False)

    # Page 0 foundation (skip), page 1 floor plan (detect), page 2 schedule (parse).
    order = [SheetType.FOUNDATION, SheetType.FLOOR_PLAN, SheetType.SCHEDULE]
    seen = {"i": 0}

    def fake_classify(page):
        kind = order[seen["i"]]
        seen["i"] += 1
        return kind

    detect_calls = {"n": 0}

    async def fake_detect(png, model):
        detect_calls["n"] += 1
        return VlmPage(entities=[], scale_text=None)

    def fake_parse_schedule(page):
        return [
            PhysicalAsset(
                type=AssetType.DOOR,
                label="D1",
                bbox=(0, 0, 10, 10),
                source="vlm-only",
                measurements={Parameter.CLEAR_WIDTH: 36.0},
            )
        ]

    monkeypatch.setattr(spatial, "classify_sheet", fake_classify)
    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    monkeypatch.setattr(spatial, "parse_schedule", fake_parse_schedule)

    row = {
        "id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
        "filename": "set.pdf", "path": str(pdf), "ingested": False,
    }
    fake_repo.documents["doc-1"] = row

    events = []

    async def emit(event):
        events.append(event)

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)

    # Detector ran once — only the floor-plan page.
    assert detect_calls["n"] == 1
    # All three pages recorded; the schedule's door is the only asset.
    assert len(fake_repo.sheets) == 3
    assert [a["type"] for a in fake_repo.assets.values()] == ["door"]
    # The foundation page was recorded but flagged not-linted.
    assert any("not linted" in e.message for e in events)
    assert any("schedule — 1 opening" in e.message for e in events)
