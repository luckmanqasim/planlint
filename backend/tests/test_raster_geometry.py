"""Raster geometry tests against a synthetic 'scan': a floor plan drawn as
pure pixels (no vector data survives rasterization), with known ground truth.

Layout of the synthetic plan (page 400x300 pt, wall thickness 4 pt):

- outer walls at x=20..24 / x=376..380 / y=276..280
- top wall y=20..24 with a 40 pt WINDOW gap at x=150..190
- internal wall x=198..202 with a 40 pt DOOR gap at y=140..180
- room A interior: (24, 24)-(198, 276); room B: (202, 24)-(376, 276)
"""

import pymupdf
import pytest

from planlint.ingest import raster_geometry as rg

ZOOM = 2.0
WALL = 4.0  # pt


def synthetic_scan_png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    black = (0, 0, 0)

    def wall(x0, y0, x1, y1):
        page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=black, fill=black)

    wall(20, 20, 150, 24)  # top, left of window gap
    wall(190, 20, 380, 24)  # top, right of window gap
    wall(20, 276, 380, 280)  # bottom (solid)
    wall(20, 20, 24, 280)  # left
    wall(376, 20, 380, 280)  # right
    wall(198, 24, 202, 140)  # internal, above door gap
    wall(198, 180, 202, 276)  # internal, below door gap

    pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
    return pix.tobytes("png")


@pytest.fixture(scope="module")
def analysis() -> rg.RasterAnalysis:
    result = rg.analyze_page_image(synthetic_scan_png(), ZOOM)
    assert result is not None
    return result


def test_thickness_estimate(analysis):
    assert 4 <= analysis.thickness_px <= 14  # 4 pt at zoom 2 = 8 px


# The page's claimed openings, as the pipeline would supply them: the top
# window and the internal door. Sealing these bounds the flood fill.
PLUGS = [(145.0, 14.0, 195.0, 30.0), (190.0, 135.0, 210.0, 185.0)]
# A generous room-A box: room fills must cover most of the claimed box.
ROOM_A_BOX = (40.0, 40.0, 180.0, 250.0)


def test_room_snap_expands_sloppy_box_to_walls(analysis):
    snapped = rg.snap_room(analysis, ROOM_A_BOX, plugs=PLUGS)
    assert snapped is not None
    bbox, area_pt2 = snapped
    x0, y0, x1, y1 = bbox
    assert abs(x0 - 24) <= 4 and abs(y0 - 24) <= 4
    assert abs(x1 - 198) <= 4 and abs(y1 - 276) <= 4
    # Interior area ≈ 174 x 252 pt.
    assert area_pt2 == pytest.approx(174 * 252, rel=0.15)


def test_room_snap_does_not_leak_through_door_gap(analysis):
    snapped = rg.snap_room(analysis, ROOM_A_BOX, plugs=PLUGS)
    assert snapped is not None
    assert snapped[0][2] <= 205  # never reaches into room B


def test_room_b_snaps_independently(analysis):
    snapped = rg.snap_room(analysis, (220.0, 40.0, 360.0, 250.0), plugs=PLUGS)
    assert snapped is not None
    x0, _, x1, _ = snapped[0]
    assert abs(x0 - 202) <= 4 and abs(x1 - 376) <= 4


def test_room_snap_directional_fallback_covers_missed_openings(analysis):
    """Even with no plugs (openings the VLM missed entirely), the directional
    closing fallback bridges this door gap (80 px <= 8x wall thickness) and
    still recovers room A without leaking into room B."""
    snapped = rg.snap_room(analysis, ROOM_A_BOX, plugs=[])
    assert snapped is not None
    x0, y0, x1, y1 = snapped[0]
    assert abs(x0 - 24) <= 4 and abs(x1 - 198) <= 4
    assert x1 <= 205


def test_window_gap_is_snapped_and_measured(analysis):
    # Claimed window roughly over the top-wall gap (true span x=150..190).
    result = rg.classify_opening(analysis, (145.0, 14.0, 195.0, 30.0))
    assert result.kind == "snapped"
    assert result.width_pt == pytest.approx(40.0, abs=4.0)
    assert result.bbox[0] == pytest.approx(150.0, abs=4.0)
    assert result.bbox[2] == pytest.approx(190.0, abs=4.0)


def test_door_gap_in_vertical_wall_is_snapped(analysis):
    result = rg.classify_opening(analysis, (190.0, 135.0, 210.0, 185.0))
    assert result.kind == "snapped"
    assert result.width_pt == pytest.approx(40.0, abs=4.0)


def test_opening_claimed_on_solid_wall_is_blocked(analysis):
    # The hatched-pier failure mode: a 'window' on an unbroken wall run.
    # Reported as informational "blocked", never acted on (dense window
    # symbols are indistinguishable from piers in the consolidated mask).
    result = rg.classify_opening(analysis, (100.0, 270.0, 140.0, 286.0))
    assert result.kind == "blocked"


def test_room_snap_rejects_fills_containing_other_rooms(analysis):
    # Without the internal door plugged, an aggressive claim box could pass
    # the ratio guards while swallowing room B — the exclusion points
    # (other claimed rooms' centers) must veto that.
    huge_claim = (30.0, 30.0, 370.0, 270.0)
    merged = rg.snap_room(analysis, huge_claim, plugs=[PLUGS[0]])
    room_b_center = (290.0, 150.0)
    vetoed = rg.snap_room(
        analysis, huge_claim, plugs=[PLUGS[0]], exclude_points_pt=[room_b_center]
    )
    if merged is not None:  # the merge is representable…
        assert vetoed is None  # …but the veto must reject it


def test_opening_with_no_wall_nearby_is_unknown(analysis):
    result = rg.classify_opening(analysis, (60.0, 60.0, 120.0, 120.0))
    assert result.kind == "unknown"


def test_area_conversion_and_agreement():
    # 100 pt² at 1 inch/pt = 100 in² = 0.0645 m².
    assert rg.fill_area_to_m2(100.0, 1.0) == pytest.approx(0.064516)
    assert rg.area_agreement(10.0, 11.0)
    assert not rg.area_agreement(10.0, 14.0)
    assert not rg.area_agreement(0.0, 5.0)
