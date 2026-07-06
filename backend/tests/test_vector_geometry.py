"""Tests for deterministic geometry: scale parsing, snapping, measurement."""

import pymupdf
import pytest

from planlint.ingest.vector_geometry import (
    Primitive,
    TextLabel,
    detect_pdf_type,
    extract_primitives,
    measure_asset,
    parse_dimension_label,
    parse_scale,
    snap_box,
)
from planlint.models import Parameter

PT = 1 / 72  # one PDF point in paper inches


# ------------------------------------------------------------- parse_scale

def test_scale_quarter_inch():
    # 1/4" on paper = 12 real inches -> factor 48 -> per PDF point 48/72
    assert parse_scale('1/4" = 1\'-0"') == pytest.approx(48 / 72)


def test_scale_eighth_inch():
    assert parse_scale('1/8" = 1\'') == pytest.approx(96 / 72)


def test_scale_ratio():
    assert parse_scale("1:50") == pytest.approx(50 / 72)


def test_scale_with_prefix_text():
    assert parse_scale('SCALE: 1/4" = 1\'-0"') == pytest.approx(48 / 72)


def test_scale_garbage():
    assert parse_scale("NOT A SCALE") is None


def test_scale_empty():
    assert parse_scale("") is None


# --------------------------------------------------- parse_dimension_label

def test_dimension_inches():
    assert parse_dimension_label('36"') == 36.0


def test_dimension_feet_inches():
    assert parse_dimension_label("3'-0\"") == 36.0


def test_dimension_feet_and_six():
    assert parse_dimension_label("2'-6\"") == 30.0


def test_dimension_plain_in():
    assert parse_dimension_label("30 in") == 30.0


def test_dimension_not_a_dimension():
    assert parse_dimension_label("FIRE EXIT") is None


def test_dimension_embedded_in_door_tag():
    assert parse_dimension_label('D1 36"') == 36.0


# ------------------------------------------------------------------ snapping

def test_snap_box_to_nearby_segments():
    primitives = [
        Primitive(p0=(100.0, 100.0), p1=(136.0, 100.0)),  # door leaf, 36 pt long
        Primitive(p0=(100.0, 100.0), p1=(100.0, 90.0)),
        Primitive(p0=(500.0, 500.0), p1=(600.0, 500.0)),  # far away wall
    ]
    vlm_box = (95.0, 85.0, 140.0, 105.0)
    snapped, was_snapped = snap_box(vlm_box, primitives)
    assert was_snapped
    # union of the two nearby segments only
    assert snapped == (100.0, 90.0, 136.0, 100.0)


def test_snap_box_no_nearby_primitives():
    primitives = [Primitive(p0=(500.0, 500.0), p1=(600.0, 500.0))]
    vlm_box = (95.0, 85.0, 140.0, 105.0)
    snapped, was_snapped = snap_box(vlm_box, primitives)
    assert not was_snapped
    assert snapped == vlm_box


# --------------------------------------------------------------- measurement

def test_measure_from_label():
    """A dimension label near the box wins over geometry."""
    labels = [TextLabel(text='30"', bbox=(140.0, 95.0, 155.0, 105.0))]
    m = measure_asset(
        asset_box=(100.0, 90.0, 136.0, 100.0),
        primitives=[],
        labels=labels,
        scale=48 / 72,
    )
    assert m is not None
    measurements, from_label = m
    assert from_label
    assert measurements[Parameter.CLEAR_WIDTH] == 30.0


def test_measure_far_label_ignored():
    labels = [TextLabel(text='30"', bbox=(400.0, 400.0, 415.0, 410.0))]
    m = measure_asset(
        asset_box=(100.0, 90.0, 136.0, 100.0),
        primitives=[],
        labels=labels,
        scale=48 / 72,
    )
    assert m is None


def test_measure_from_geometry():
    """Longest segment inside the box × scale. 54 pt at 1/4"=1'-0" (×48/72) = 36 in."""
    primitives = [
        Primitive(p0=(100.0, 100.0), p1=(154.0, 100.0)),  # 54 pt
        Primitive(p0=(100.0, 100.0), p1=(100.0, 110.0)),  # 10 pt
    ]
    m = measure_asset(
        asset_box=(95.0, 90.0, 160.0, 115.0),
        primitives=primitives,
        labels=[],
        scale=48 / 72,
    )
    assert m is not None
    measurements, from_label = m
    assert not from_label
    assert measurements[Parameter.CLEAR_WIDTH] == pytest.approx(36.0)


def test_measure_no_scale_no_label():
    """Without a scale, geometry cannot produce real-world inches."""
    primitives = [Primitive(p0=(100.0, 100.0), p1=(154.0, 100.0))]
    m = measure_asset(
        asset_box=(95.0, 90.0, 160.0, 115.0),
        primitives=primitives,
        labels=[],
        scale=None,
    )
    assert m is None


# ------------------------------------------------------- pdf type detection

def _vector_page(doc):
    page = doc.new_page(width=612, height=792)
    for i in range(5):
        page.draw_line((50, 50 + i * 10), (500, 50 + i * 10))
    return page


def test_detect_vector_pdf():
    doc = pymupdf.open()
    page = _vector_page(doc)
    assert detect_pdf_type(page) == "vector"


def test_detect_raster_pdf():
    # A page with no vector drawings (as a scan would be: just an image)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    assert detect_pdf_type(page) == "raster"


def test_extract_primitives_and_labels():
    doc = pymupdf.open()
    page = _vector_page(doc)
    page.insert_text((100, 200), 'SCALE: 1/4" = 1\'-0"', fontsize=10)
    primitives, labels = extract_primitives(page)
    assert len(primitives) >= 5
    assert any("SCALE" in label.text for label in labels)
