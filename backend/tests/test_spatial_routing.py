"""Sheet-type routing in floor-plan ingestion: the plan-view detector runs only
on floor-plan pages, schedules are parsed for widths, and every other sheet is
recorded but not linted. No real vision calls."""

from __future__ import annotations

import pymupdf
import pytest

from planlint.ingest import spatial
from planlint.ingest.sheet_type import SheetType
from planlint.ingest.vlm import VlmEntity, VlmPage
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


async def test_phantom_space_without_name_area_or_walls_is_dropped(
    tmp_path, fake_repo, monkeypatch
):
    # On a blank (wall-less) page, a room with no name and no area has no
    # geometric support — it's unrelated lines boxed as a space, and is dropped.
    # A named room on the same page is kept.
    pdf = tmp_path / "plan.pdf"
    _blank_pdf(pdf, pages=1)
    monkeypatch.setattr(spatial.settings, "planlint_fake_llm", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[
                VlmEntity(entity_type=AssetType.ROOM, name="", box=(50, 50, 200, 200)),
                VlmEntity(entity_type=AssetType.ROOM, name="KITCHEN", box=(60, 60, 210, 210)),
            ],
            scale_text=None,
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)

    row = {
        "id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
        "filename": "plan.pdf", "path": str(pdf), "ingested": False,
    }
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)

    rooms = [a["label"] for a in fake_repo.assets.values() if a["type"] == "room"]
    assert rooms == ["KITCHEN"]  # the unnamed, wall-less phantom was dropped


def _room_pdf(path):
    """A page whose only geometry is four walls forming a box in PDF points."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100, 100), (300, 100))  # top
    page.draw_line((100, 200), (300, 200))  # bottom
    page.draw_line((100, 100), (100, 200))  # left
    page.draw_line((300, 100), (300, 200))  # right
    doc.save(str(path))
    doc.close()


async def _ingest_entity(pdf, fake_repo, monkeypatch, entity):
    """Ingest a single-page plan whose detector returns exactly `entity`; return
    all asset rows written to the repo."""
    monkeypatch.setattr(spatial.settings, "planlint_fake_llm", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(entities=[entity], scale_text=None)

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {
        "id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
        "filename": "plan.pdf", "path": str(pdf), "ingested": False,
    }
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    return list(fake_repo.assets.values())


async def test_wall_snapped_but_nameless_room_is_dropped(tmp_path, fake_repo, monkeypatch):
    # A nameless, area-less box that snaps to real walls (vector-snapped) is
    # still dropped — for rooms, geometry alone isn't enough.
    pdf = tmp_path / "plan.pdf"
    _room_pdf(pdf)
    entity = VlmEntity(entity_type=AssetType.ROOM, name="", box=(105, 105, 295, 195))
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert assets == []


async def test_unnamed_room_with_printed_area_is_kept(tmp_path, fake_repo, monkeypatch):
    # The "OR printed area" branch: an unnamed room that carries an area survives.
    pdf = tmp_path / "plan.pdf"
    _room_pdf(pdf)
    entity = VlmEntity(
        entity_type=AssetType.ROOM, name="", floor_area_m2=18.0, box=(105, 105, 295, 195)
    )
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert len(assets) == 1
    assert assets[0]["measurements"]["area_m2"] == 18.0


async def test_bare_vlm_guess_is_dropped(tmp_path, fake_repo, monkeypatch):
    # A window the VLM boxed on a blank page: no name, no measurement, and no
    # geometry it snapped to (vlm-only). It is noise — dropped.
    pdf = tmp_path / "plan.pdf"
    _blank_pdf(pdf, pages=1)
    entity = VlmEntity(entity_type=AssetType.WINDOW, name="", box=(50.0, 50.0, 120.0, 60.0))
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert assets == []

async def test_ramp_slope_read_from_label(tmp_path, fake_repo, monkeypatch):
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((110, 230), "SLOPE 1:12", fontsize=8)  # slope annotation on the ramp
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_fake_llm", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.RAMP, name="RAMP", box=(100, 200, 220, 260))],
            scale_text=None,
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "plan.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    ramp = next(a for a in fake_repo.assets.values() if a["type"] == "ramp")
    assert ramp["measurements"]["slope"] == pytest.approx(0.083, abs=0.001)


async def test_snapped_door_without_name_or_measurement_is_kept(tmp_path, fake_repo, monkeypatch):
    # A door in a real wall gap snaps (vector-snapped) and is kept even without a
    # name or measurement — geometry confirms it, so it is not a bare guess.
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100.0, 200.0), (250.0, 200.0))  # wall left of the door
    page.draw_line((286.0, 200.0), (440.0, 200.0))  # wall right of the door (gap 250..286)
    doc.save(str(pdf))
    doc.close()
    entity = VlmEntity(entity_type=AssetType.DOOR, name="", box=(245.0, 192.0, 291.0, 208.0))
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert len(assets) == 1
    assert assets[0]["type"] == "door"
    assert assets[0]["source"] == "vector-snapped"


async def test_refuted_opening_is_dropped_and_snapped_uses_gap_width(tmp_path, fake_repo, monkeypatch):
    from planlint.ingest.sheet_type import SheetType
    from planlint.ingest.vlm import VlmEntity, VlmPage
    from planlint.models import AssetType

    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # horizontal wall @ y=300 broken between x=350 and x=386 (36pt window gap)
    page.draw_line((200, 300), (350, 300))
    page.draw_line((386, 300), (520, 300))
    page.draw_line((350, 303), (386, 303))  # glazing in the gap
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_fake_llm", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[
                VlmEntity(entity_type=AssetType.WINDOW, name="", box=(210, 292, 500, 308)),
            ],
            scale_text='SCALE: 1/4" = 1\'-0"',
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "plan.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    windows = [a for a in fake_repo.assets.values() if a["type"] == "window"]
    assert len(windows) == 1
    w = windows[0]
    assert w["bbox"][2] - w["bbox"][0] == pytest.approx(36.0, abs=1.0)
    assert w["source"] == "vector-snapped"
