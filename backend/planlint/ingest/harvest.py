"""Harvest a grounded dimension from the sheet a plan asset references.

Phase 1 links a plan asset to the detail/section that draws it. This module reads
the dimension actually printed on that target sheet — via the deterministic
dimension grid (`ingest/dimensions.py`), so a value is trusted only when it is
printed AND validated against its dimension line's length × scale. Selection is
deliberately conservative: a parameter is harvested only when exactly one grounded
dimension falls in its typical range, so an ambiguous detail sheet yields nothing
(→ NEEDS_REVIEW downstream) rather than a wrong number. No LLM, no estimation.
"""

from __future__ import annotations

from planlint.ingest import dimensions
from planlint.ingest import vector_geometry as geometry
from planlint.models import Parameter

# Typical real-inch range a harvested value must fall in to be attributed to a
# parameter. Tighter than elevation.py's sane bounds so a stair detail's riser
# (~7") and tread (~11") don't both match the same parameter — the ranges are
# chosen not to overlap for the pair a single element carries.
_SELECT_RANGE: dict[Parameter, tuple[float, float]] = {
    Parameter.RISER_HEIGHT: (4.0, 8.0),
    Parameter.TREAD_DEPTH: (9.0, 14.0),
    Parameter.CLEAR_WIDTH: (18.0, 48.0),
    Parameter.OPENING_HEIGHT: (60.0, 96.0),
    Parameter.THRESHOLD_HEIGHT: (0.0, 2.0),
    Parameter.MANEUVERING_CLEARANCE: (12.0, 72.0),
    Parameter.LANDING_LENGTH: (36.0, 120.0),
}


def harvest_measurements(page, want: set[Parameter]) -> dict[Parameter, float]:
    """Grounded measurements for the wanted parameters, read off `page`'s printed
    dimensions. A parameter is returned only when exactly one grounded dimension
    lands in its typical range; 0 or several → omitted, never guessed."""
    primitives, labels = geometry.extract_primitives(page)
    scale_text = next((lbl.text for lbl in labels if "SCALE" in lbl.text.upper()), None)
    scale = geometry.parse_scale(scale_text or "")
    if scale is None:  # can't ground a dimension without the sheet's scale
        return {}
    grid = dimensions.build_dimension_grid(primitives, labels, scale)

    out: dict[Parameter, float] = {}
    for param in want:
        rng = _SELECT_RANGE.get(param)
        if rng is None:
            continue
        lo, hi = rng
        values = sorted({round(d.value_in, 1) for d in grid if lo <= d.value_in <= hi})
        if len(values) == 1:  # a single unambiguous grounded candidate
            out[param] = values[0]
    return out
