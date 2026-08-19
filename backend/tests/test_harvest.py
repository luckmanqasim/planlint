"""Dimension harvest: read a grounded dimension off a referenced detail sheet and
attribute it to the referring asset. Conservative — a single unambiguous grounded
value only, and never overriding a plan measurement."""

from __future__ import annotations

import pymupdf

from planlint.ingest import spatial
from planlint.ingest.harvest import harvest_measurements
from planlint.ingest.sheet_type import SheetType
from planlint.models import AssetType, Parameter

# 3/4" = 1'-0"  →  scale = 16/72 in/pt  →  1 in = 4.5 pt.
SCALE_TEXT = 'SCALE: 3/4" = 1\'-0"'


def _detail_page(dims):
    """A page with a scale label and, per (text, value_in), a horizontal dimension
    line of the right length with the value printed just above it."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((60, 60), SCALE_TEXT, fontsize=9)
    y = 200
    for text, value in dims:
        page.draw_line((100, y), (100 + value * 4.5, y))
        page.insert_text((100, y - 6), text, fontsize=8)
        y += 60
    return doc, page


def test_harvest_single_grounded_value():
    doc, page = _detail_page([('7"', 7.0)])
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {Parameter.RISER_HEIGHT: 7.0}


def test_harvest_riser_and_tread_pair():
    doc, page = _detail_page([('7"', 7.0), ('11"', 11.0)])
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT, Parameter.TREAD_DEPTH})
    doc.close()
    assert got == {Parameter.RISER_HEIGHT: 7.0, Parameter.TREAD_DEPTH: 11.0}


def test_harvest_skips_when_ambiguous():
    # two values both in the riser range → can't tell which → nothing harvested
    doc, page = _detail_page([('7"', 7.0), ('6"', 6.0)])
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {}


def test_harvest_skips_out_of_range():
    doc, page = _detail_page([('20"', 20.0)])  # not a plausible riser
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {}


def test_harvest_needs_scale():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100, 200), (131.5, 200))
    page.insert_text((100, 194), '7"', fontsize=8)  # no SCALE label on the page
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {}


async def test_harvest_enriches_referring_stair(tmp_path, fake_repo, monkeypatch):
    from planlint.ingest.vlm import VlmEntity, VlmPage, VlmReference

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", True)
    pdf = tmp_path / "set.pdf"
    doc = pymupdf.open()
    p0 = doc.new_page(width=612, height=792)  # plan: a stair + a detail callout
    p0.insert_text((305, 262), "3/A1.7", fontsize=8)  # grounds the target in the text layer
    p1 = doc.new_page(width=612, height=792)  # A1.7 STAIR DETAIL with a 7" riser
    p1.insert_text((60, 60), SCALE_TEXT, fontsize=9)
    p1.insert_text((100, 100), "STAIR DETAIL", fontsize=14)
    p1.insert_text((520, 740), "A1.7", fontsize=12)  # the detail's own sheet number
    p1.draw_line((100, 300), (131.5, 300))
    p1.insert_text((100, 294), '7"', fontsize=8)
    doc.save(str(pdf))
    doc.close()

    def fake_from_labels(labels):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.STAIR, name="STAIR", box=(300, 200, 340, 260))],
            references=[VlmReference(kind="detail", detail_num="3", target_sheet="A1.7", box=(305, 262, 320, 272))],
        )

    order = [SheetType.FLOOR_PLAN, SheetType.DETAIL]
    seen = {"i": 0}

    def fake_classify(page):
        t = order[min(seen["i"], len(order) - 1)]
        seen["i"] += 1
        return t

    monkeypatch.setattr(spatial, "detect_from_labels", fake_from_labels)
    monkeypatch.setattr(spatial, "classify_sheet", fake_classify)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "set.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None)

    stair = next(a for a in fake_repo.assets.values() if a["type"] == "stair")
    assert stair["measurements"] == {"riser_height": 7.0}
    assert stair["source"] == "detail-referenced"
