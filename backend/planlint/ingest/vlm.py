"""Vision agent: classifies physical entities on a rendered plan page.

The VLM's boxes are treated as approximate — on vector PDFs they are snapped
to the exact geometry afterwards (see spatial.py). Rendering zoom is handled
here so callers speak PDF points only.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from planlint.models import AssetType, BBox

import struct

RENDER_ZOOM = 2.0  # pixels per PDF point when rasterizing pages for the VLM


class VlmEntity(BaseModel):
    entity_type: AssetType
    label: str = ""
    box: tuple[float, float, float, float]  # pixels in the rendered image


class VlmPage(BaseModel):
    entities: list[VlmEntity] = Field(default_factory=list)
    scale_text: str | None = None  # e.g. 'SCALE: 1/4" = 1\'-0"' from the title block


_INSTRUCTIONS = f"""\
You analyze architectural floor plan drawings.

Identify every physical entity relevant to building-code compliance:
{", ".join(t.value for t in AssetType if t != AssetType.OTHER)}.
Doors marked as fire/emergency exits are fire_exit, not door.

For each entity return:
- entity_type (from the list above),
- label: any identifying text printed near it (e.g. 'D1', 'FIRE EXIT'),
- box: [ymin, xmin, ymax, xmax] normalized to 1000 (0-1000 scale), tight around the entity.

Also read the drawing scale from the title block if present and return it
verbatim as scale_text (e.g. 'SCALE: 1/4" = 1\'-0"'), else null.
"""


async def detect_page(image_png: bytes, model) -> VlmPage:
    """Run the VLM over one rendered page; boxes converted to PDF points."""
    # PNG dimensions start at byte offset 16 (4 bytes width, 4 bytes height)
    img_width, img_height = struct.unpack(">II", image_png[16:24])
    agent = Agent(model, output_type=VlmPage, instructions=_INSTRUCTIONS)
    result = await agent.run(
        [
            "Detect all compliance-relevant entities on this floor plan page.",
            BinaryContent(data=image_png, media_type="image/png"),
        ]
    )
    page = result.output
    scaled = []
    for entity in page.entities:
        ymin, xmin, ymax, xmax = entity.box
        # De-normalize from [0, 1000] scale to image pixels, then scale to PDF points
        x0 = (xmin / 1000.0) * img_width / RENDER_ZOOM
        y0 = (ymin / 1000.0) * img_height / RENDER_ZOOM
        x1 = (xmax / 1000.0) * img_width / RENDER_ZOOM
        y1 = (ymax / 1000.0) * img_height / RENDER_ZOOM
        
        scaled.append(entity.model_copy(update={"box": (x0, y0, x1, y1)}))
    return VlmPage(entities=scaled, scale_text=page.scale_text)


def fake_detect_from_labels(labels) -> VlmPage:
    """Deterministic stand-in for the VLM (PLANLINT_FAKE_LLM=1): derives
    entities from CAD text labels. Door labels look like 'D1 36\"'; a
    'FIRE EXIT' label marks a fire exit. Used by tests and the offline demo
    path — never in real deployments."""
    entities: list[VlmEntity] = []
    scale_text = None
    for label in labels:
        text = label.text.strip()
        upper = text.upper()
        if "SCALE" in upper:
            scale_text = text
        elif "FIRE EXIT" in upper:
            entities.append(
                VlmEntity(entity_type=AssetType.FIRE_EXIT, label=text, box=label.bbox)
            )
        elif upper.startswith("D") and '"' in text:
            entities.append(VlmEntity(entity_type=AssetType.DOOR, label=text, box=label.bbox))
    return VlmPage(entities=entities, scale_text=scale_text)
