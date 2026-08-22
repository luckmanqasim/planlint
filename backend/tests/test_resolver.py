"""Transitive resolver: follow a nested callout across sheets and pull a deep
dimension back to the origin asset. detect_details / read_region are stubbed (VLM);
nested-callout discovery runs for real against the synthetic text layer."""

from __future__ import annotations

import pymupdf

from planlint.ingest import resolver
from planlint.ingest.details import VlmDetail
from planlint.ingest.resolver import resolve_reference_chain
from planlint.models import AssetType, Parameter, PhysicalAsset


async def test_resolver_follows_nested_chain(monkeypatch):
    doc = pymupdf.open()
    a32 = doc.new_page(width=612, height=792)  # index 0 = A3.2 (section)
    a32.insert_text((150, 300), "SECTION: 6/A4.1", fontsize=8)  # nested callout
    a41 = doc.new_page(width=612, height=792)  # index 1 = A4.1 (detail)
    a41.insert_text((150, 300), 'TREAD 7 1/2"', fontsize=8)

    page_of_number = {"A3.2": 0, "A4.1": 1}
    registry = {"A3.2": "SECTION", "A4.1": "DETAILS"}

    async def fake_detect_details(png, model):
        return [
            VlmDetail(number="1", title="", box=(0, 0, 612, 792)),
            VlmDetail(number="6", title="", box=(0, 0, 612, 792)),
        ]

    async def fake_read_region(page, region, want, text_layer, model):
        return {Parameter.TREAD_DEPTH: 7.5} if "TREAD" in page.get_text().upper() else {}

    monkeypatch.setattr(resolver, "detect_details", fake_detect_details)
    monkeypatch.setattr(resolver, "read_region", fake_read_region)

    stair = PhysicalAsset(type=AssetType.STAIR, label="S1", bbox=(0, 0, 10, 10))
    details, fills = await resolve_reference_chain(
        doc, "model", page_of_number, registry,
        seeds=[(stair, "A3.2", "1", "section")],
        harvest_params={AssetType.STAIR: {Parameter.RISER_HEIGHT, Parameter.TREAD_DEPTH}},
    )
    doc.close()

    keys = {(d.sheet_number, d.number, d.depth) for d in details}
    assert ("A3.2", "1", 0) in keys  # the directly-referenced section
    assert ("A4.1", "6", 1) in keys  # followed one hop from the section
    # the tread two hops down reached the stair
    assert fills[stair.id][Parameter.TREAD_DEPTH] == 7.5
    a41 = next(d for d in details if d.sheet_number == "A4.1")
    assert a41.parent_sheet == "A3.2" and a41.parent_number == "1"


async def test_resolver_dedups_and_caps(monkeypatch):
    doc = pymupdf.open()
    # A self-referential loop: A1.7 detail 1's region points back to 1/A1.7.
    p = doc.new_page(width=612, height=792)
    p.insert_text((150, 300), "SEE 1/A1.7", fontsize=8)

    async def fake_detect_details(png, model):
        return [VlmDetail(number="1", title="", box=(0, 0, 612, 792))]

    async def fake_read_region(page, region, want, text_layer, model):
        return {}

    monkeypatch.setattr(resolver, "detect_details", fake_detect_details)
    monkeypatch.setattr(resolver, "read_region", fake_read_region)

    stair = PhysicalAsset(type=AssetType.STAIR, label="S1", bbox=(0, 0, 10, 10))
    details, _ = await resolve_reference_chain(
        doc, "model", {"A1.7": 0}, {"A1.7": "DETAIL"},
        seeds=[(stair, "A1.7", "1", "detail")],
        harvest_params={AssetType.STAIR: {Parameter.TREAD_DEPTH}},
    )
    doc.close()
    # the loop is visited once, not infinitely
    assert len(details) == 1
