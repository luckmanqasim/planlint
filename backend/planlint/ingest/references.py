"""Ground and bind cross-sheet reference markers.

The VLM classifies a marker (section / detail / elevation) and reads its target
sheet; this module is the deterministic half of that seam. `ground_reference`
accepts a target only when it is corroborated by the page text layer AND/OR the
sheet-index registry — a hallucinated or mistyped sheet id is dropped, never
linked. `nearest_asset` then binds a grounded marker to the plan asset it sits on
(proximity), so a detail bubble on a door links that door to its detail sheet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from planlint.ingest import vector_geometry as geometry
from planlint.ingest.sheet_type import SHEET_NUMBER
from planlint.models import BBox, PhysicalAsset, SheetReference

# A reference callout drawn as text ('3/A1.7'). Detail sheets print these to point at
# further details ("SECTION: 5/A4.0"), so scanning them lets the resolver follow a
# chain. Deterministic — they are in the text layer, so no VLM is spent on discovery.
_REF_RE = re.compile(r"^(\d+)\s*/\s*([A-Z]{1,2}\d{1,2}\.\d{1,2})$", re.IGNORECASE)
_KIND_WORD = {
    "PLAN": "detail", "DETAIL": "detail",
    "ELEVATION": "elevation", "SECTION": "section",
}

# A reference marker this close (PDF points) to an asset box is taken to reference
# that asset (a detail bubble drawn over a door, a section cut beside a stair).
_REF_RADIUS_PT = 40.0


def _canonical_sheet(text: str) -> str | None:
    """The canonical sheet number inside a VLM string ('3/A3.0', 'A-3.0 ')."""
    m = SHEET_NUMBER.search(text.upper().replace(" ", ""))
    return m.group(1) if m else None


def ground_reference(
    kind: str,
    detail_num: str,
    target_sheet: str,
    bbox: BBox,
    page_text_upper: str,
    registry: dict[str, str],
) -> SheetReference | None:
    """A `SheetReference` when the VLM's target is real, else None.

    The target must be corroborated: printed on this page's text layer and/or a key
    in the sheet-index registry. Both → high confidence; one → medium; neither →
    dropped (an ungrounded model guess never becomes a link).
    """
    target = _canonical_sheet(target_sheet)
    if target is None:
        return None
    in_text = target in page_text_upper.upper()
    in_registry = target in registry
    if not in_text and not in_registry:
        return None
    return SheetReference(
        kind=kind,  # type: ignore[arg-type]  # validated by the model's Literal
        detail_num=detail_num.strip(),
        target_sheet_number=target,
        bbox=bbox,
        confidence=0.9 if (in_text and in_registry) else 0.6,
    )


def _center_in(bbox: BBox, region: BBox) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def detect_text_references(
    page, registry: dict[str, str], region: BBox | None = None
) -> list[SheetReference]:
    """Grounded reference callouts printed on `page` (optionally only inside
    `region`) — the deterministic half of chain-following. Each `N/SHEET` token is
    grounded against the text layer + registry; a `PLAN:/ELEVATION:/SECTION:` word to
    its left sets the kind. Used to discover the details a detail itself references."""
    words = page.get_text("words")  # (x0, y0, x1, y1, text, block, line, word)
    text_upper = page.get_text().upper()
    out: list[SheetReference] = []
    for w in words:
        m = _REF_RE.match(w[4].strip().strip(":,;"))
        if not m:
            continue
        bbox = (w[0], w[1], w[2], w[3])
        if region is not None and not _center_in(bbox, region):
            continue
        # kind from a label word to the left on the same block+line
        kind = "detail"
        for prev in words:
            if prev[5] == w[5] and prev[6] == w[6] and prev[0] < w[0]:
                key = prev[4].strip().strip(":").upper()
                if key in _KIND_WORD:
                    kind = _KIND_WORD[key]
        sref = ground_reference(kind, m.group(1), m.group(2).upper(), bbox, text_upper, registry)
        if sref is not None:
            out.append(sref)
    return out


def nearest_asset(
    ref_bbox: BBox, assets: Sequence[PhysicalAsset]
) -> PhysicalAsset | None:
    """The plan asset a marker references — the nearest within `_REF_RADIUS_PT`, or
    None (a section cut through open plan references no single asset)."""
    best: PhysicalAsset | None = None
    best_d = _REF_RADIUS_PT + 1.0
    for asset in assets:
        d = geometry._box_distance(ref_bbox, asset.bbox)
        if d <= _REF_RADIUS_PT and d < best_d:
            best, best_d = asset, d
    return best
