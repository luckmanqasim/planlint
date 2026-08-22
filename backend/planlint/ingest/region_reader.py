"""General spatial dimension reader: read the printed dimensions of one detail.

Given a crop of a single detail region and the parameters an asset needs, the VLM
cites each dimension VERBATIM — including one drawn spatially between geometry (a
tread dimension between two stair lines) that OCR/text-grid can't attribute. It
stays honest by the same seam as ingest/elevation.py: a cited value is accepted only
when it appears in the page's text layer AND parses into the parameter's plausible
range. The model supplies the spatial association; the text layer proves the number
is printed. It never estimates.
"""

from __future__ import annotations

import pymupdf
from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext

from planlint.ingest.elevation import _normalize, parse_inches
from planlint.ingest.vlm import RENDER_ZOOM
from planlint.models import BBox, Parameter

# Parameter → (human label the VLM is asked for, plausible real-inch range). Ranges
# are wide (misread-only), so a non-compliant value is still read and later flagged.
_PARAM_INFO: dict[Parameter, tuple[str, tuple[float, float]]] = {
    Parameter.RISER_HEIGHT: ("riser height", (2.0, 14.0)),
    Parameter.TREAD_DEPTH: ("tread depth", (5.0, 24.0)),
    Parameter.CLEAR_WIDTH: ("clear width", (8.0, 60.0)),
    Parameter.OPENING_HEIGHT: ("opening height", (48.0, 144.0)),
    Parameter.THRESHOLD_HEIGHT: ("threshold height", (0.0, 6.0)),
    Parameter.LANDING_LENGTH: ("landing length", (24.0, 240.0)),
    Parameter.MANEUVERING_CLEARANCE: ("maneuvering clearance", (6.0, 96.0)),
}


class RegionDims(BaseModel):
    # param label (exactly as asked) → dimension printed in the detail, verbatim
    measurements: dict[str, str] = Field(default_factory=dict)


def build_region_agent(model, wanted_labels: list[str], label_to_param: dict, text_layer: str) -> Agent:
    listing = ", ".join(wanted_labels)
    instructions = (
        "You are shown a crop of ONE architectural detail (a section, elevation, or "
        "plan detail). Read ONLY the printed dimensions for these quantities, using "
        "the drawing's spatial layout to decide which number labels which quantity: "
        f"{listing}.\n"
        "Return a mapping from the quantity name (exactly as listed) to the dimension "
        "printed for it, copied VERBATIM (e.g. '7 1/4\"', \"2'-8\\\"\"). Omit any "
        "quantity whose dimension is not printed in this detail. Never estimate or "
        "compute a value."
    )
    agent = Agent(
        model,
        output_type=RegionDims,
        deps_type=str,
        instructions=instructions,
        model_settings={"temperature": 0.0},
        retries=2,
    )

    @agent.output_validator
    async def verify(ctx: RunContext[str], output: RegionDims) -> RegionDims:
        layer = _normalize(ctx.deps)
        problems: list[str] = []
        for name, text in output.measurements.items():
            param = label_to_param.get(name)
            if param is None:
                continue  # a quantity we didn't ask for — ignored, not an error
            if _normalize(text) and _normalize(text) not in layer:
                problems.append(
                    f"{name}={text!r} is not printed on the page — cite only "
                    "dimensions the drawing actually shows"
                )
                continue
            value = parse_inches(text)
            lo, hi = _PARAM_INFO[param][1]
            if value is None or not (lo <= value <= hi):
                problems.append(f"{name}={text!r} is not a plausible {name}")
        if problems:
            raise ModelRetry("Correct these dimensions:\n" + "\n".join(problems))
        return output

    return agent


async def read_region(
    page, region: BBox, want: set[Parameter], text_layer: str, model
) -> dict[Parameter, float]:
    """Grounded measurements read from a single detail's region. A value is returned
    only when the VLM cited it, it appears in the text layer, and it parses into the
    parameter's plausible range — otherwise omitted (→ NEEDS_REVIEW), never guessed."""
    wanted = [(_PARAM_INFO[p][0], p) for p in want if p in _PARAM_INFO]
    if not wanted:
        return {}
    label_to_param = {label: param for label, param in wanted}
    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM), clip=pymupdf.Rect(region)
    )
    agent = build_region_agent(model, [label for label, _ in wanted], label_to_param, text_layer)
    result = await agent.run(
        [
            "Read the printed dimensions in this detail.",
            BinaryContent(data=pix.tobytes("png"), media_type="image/png"),
        ],
        deps=text_layer,
    )
    out: dict[Parameter, float] = {}
    for name, text in result.output.measurements.items():
        param = label_to_param.get(name)
        if param is None:
            continue
        value = parse_inches(text)
        lo, hi = _PARAM_INFO[param][1]
        if value is not None and lo <= value <= hi:
            out[param] = round(value, 2)
    return out
