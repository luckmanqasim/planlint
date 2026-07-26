"""Vision agent: classifies physical entities on a rendered plan page.

The VLM's boxes are treated as approximate — on vector PDFs they are snapped
to the exact geometry afterwards (see spatial.py). Rendering zoom is handled
here so callers speak PDF points only.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry

from planlint.models import AssetType, BBox

import re
import struct

RENDER_ZOOM = 2.0  # pixels per PDF point when rasterizing pages for the VLM

# Printed areas ('39.37 m²', '12 m2', '450 sq ft') do not belong in names.
_AREA_IN_NAME = re.compile(r"\d\s*(?:m²|m2\b|sq\s*\.?\s*(?:m|ft)|ft²|ft2\b)", re.IGNORECASE)

_SPACE_TYPES = (AssetType.ROOM, AssetType.CORRIDOR)
# A bare number ('1') or single letter ('D') labelling a room is a callout /
# detail-marker bubble (often on a dashed section-cut line), not a space name.
_ROOM_CALLOUT = re.compile(r"^(?:\d+|[A-Za-z])$")


def _resolve_space_name(entity: "VlmEntity") -> str | None:
    """The room/corridor name cleaned of callouts, or None to drop the entity.

    A bare callout ('A', '1') labelling a space is a section/detail cut marker,
    not a room. With no printed area it is a phantom room — dropped entirely.
    With an area (a numbered room) the callout name is blanked but the space
    kept. Non-space entities pass through unchanged."""
    if entity.entity_type not in _SPACE_TYPES:
        return entity.name
    if _ROOM_CALLOUT.match(entity.name.strip()):
        return "" if entity.floor_area_m2 else None
    return entity.name


class VlmEntity(BaseModel):
    entity_type: AssetType
    name: str = ""  # clean identifier only ('GARAGE', 'D1') — no dimensions
    floor_area_m2: float | None = None  # printed floor area, rooms only
    box: tuple[float, float, float, float]  # pixels in the rendered image


class VlmPage(BaseModel):
    entities: list[VlmEntity] = Field(default_factory=list)
    scale_text: str | None = None  # e.g. 'SCALE: 1/4" = 1\'-0"' from the title block


_INSTRUCTIONS = f"""\
You analyze architectural floor plan drawings.

Identify every physical entity relevant to building-code compliance:
{", ".join(t.value for t in AssetType if t != AssetType.OTHER)}.
Doors marked as fire/emergency exits are fire_exit, not door.

Drawing conventions — apply them strictly before classifying:
- Walls are thick double lines; solid or masonry wall sections are filled
  with diagonal cross-hatching. A hatched or filled wall segment is NEVER a
  window or a door. Never report a wall itself as an entity.
- window: a thin opening in a wall drawn as 2-3 parallel lines across the
  wall thickness (often with a sill line). A window's interior is always
  white/unhatched: if diagonal hatching or solid fill runs through the
  shape, it is wall — not a window. Small numbers printed on wall segments
  (e.g. '22') are wall-thickness callouts, not window marks.
- door: a wall opening with a quarter-circle swing arc. A garage door is a
  wide straight opening spanning most of a garage wall with no swing arc
  (usually a single thin panel line, wide enough for a vehicle) — classify
  it as door and name it 'GARAGE DOOR' unless the drawing names it.
  A garage almost always has at least one garage door on an exterior wall:
  inspect the garage perimeter for long, thin, unhatched openings before
  finishing — two side by side separated by a short hatched pier means two
  garage doors.
- wall piers: a short hatched or solid wall stub between two openings (for
  example between two garage doors) is wall — never a window.
- stair: a run of parallel treads, usually with a direction arrow.
- room: a named enclosed space bounded by walls. A callout/detail bubble — a
  small circle or hexagon holding a letter or number ('A', '1'), often sitting
  on a dashed or dotted section/detail cut line — is NOT a room and NOT a
  physical entity; never report it (as a room or anything else).

Only report an entity you are confident about; omit anything ambiguous
rather than guessing.

For each entity return:
- entity_type (from the list above).
- name: the entity's clean identifier exactly as printed — a room name
  ('GARAGE', 'CHAMBRE 2'), a door code ('D1'), or 'GARAGE DOOR' / 'FIRE
  EXIT'. NEVER include areas, dimension strings, wall-thickness numbers, or
  any other annotation text in the name. Empty string if unnamed.
  For a room, the name is its descriptive space name in words ('KITCHEN',
  'BEDROOM 2'). It is NEVER a callout or detail-marker bubble (a circled
  number or single letter such as '1' or 'D') or a door tag — those are not
  room names. If a room shows no descriptive name, return an empty string.
- floor_area_m2: for rooms, the printed floor area as a bare number (39.37
  from '39.37 m²'); null when not printed or not a room.
- box: [ymin, xmin, ymax, xmax] normalized to 1000 (0-1000 scale) covering
  the entity's FULL PHYSICAL EXTENT:
  * room: the entire interior from wall to wall — never just the room's
    name text. A room's box is usually large.
  * door / window / garage door: the whole opening across the wall
    thickness.
  * stair: the complete flight of treads.

Also read the drawing scale from the title block if present and return it
verbatim as scale_text (e.g. 'SCALE: 1/4" = 1\'-0"'), else null.
"""


def build_detection_agent(model) -> Agent:
    """VLM agent with an output validator: malformed boxes (inverted or
    out-of-range coordinates) are bounced back with ModelRetry instead of
    corrupting the overlay."""
    # temperature 0: detection should be reproducible run-to-run.
    # retries=2 (above the default 1): a busy or non-plan-view page often needs
    # a second correction before the boxes validate; the caller isolates the
    # page if even that fails, so exhausting retries never sinks the run.
    agent = Agent(
        model,
        output_type=VlmPage,
        instructions=_INSTRUCTIONS,
        model_settings={"temperature": 0.0},
        retries=2,
    )

    @agent.output_validator
    async def validate_boxes(output: VlmPage) -> VlmPage:
        problems: list[str] = []
        room_areas: list[float] = []
        for i, entity in enumerate(output.entities):
            ymin, xmin, ymax, xmax = entity.box
            where = f"entities[{i}] ({entity.entity_type.value} {entity.name!r})"
            if not (0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000):
                problems.append(
                    f"{where}: box {entity.box} is not [ymin, xmin, ymax, xmax] "
                    "on the 0-1000 scale with min < max"
                )
                continue
            if _AREA_IN_NAME.search(entity.name):
                problems.append(
                    f"{where}: name {entity.name!r} contains a printed area — "
                    "put the number in floor_area_m2 and keep name clean"
                )
            if entity.entity_type == AssetType.ROOM:
                room_areas.append((ymax - ymin) * (xmax - xmin))
        # Rooms boxed around their name text instead of their walls: on the
        # 0-1000 grid a text label is a sliver, while a plan's largest room
        # always covers well over 1.5% of the page.
        if room_areas and max(room_areas) < 15_000:
            problems.append(
                "every room box is label-sized — each room's box must cover "
                "the room's full interior from wall to wall, not its printed "
                "name"
            )
        if problems:
            raise ModelRetry(
                "Invalid entities — re-emit all entities corrected:\n"
                + "\n".join(problems)
            )
        return output

    return agent


async def detect_page(image_png: bytes, model) -> VlmPage:
    """Run the VLM over one rendered page; boxes converted to PDF points."""
    # PNG dimensions start at byte offset 16 (4 bytes width, 4 bytes height)
    img_width, img_height = struct.unpack(">II", image_png[16:24])
    agent = build_detection_agent(model)
    result = await agent.run(
        [
            "Detect all compliance-relevant entities on this floor plan page.",
            BinaryContent(data=image_png, media_type="image/png"),
        ]
    )
    page = result.output
    scaled = []
    for entity in page.entities:
        name = _resolve_space_name(entity)
        if name is None:
            continue  # a bare callout marker mis-detected as a space — drop it
        ymin, xmin, ymax, xmax = entity.box
        # De-normalize from [0, 1000] scale to image pixels, then scale to PDF points
        x0 = (xmin / 1000.0) * img_width / RENDER_ZOOM
        y0 = (ymin / 1000.0) * img_height / RENDER_ZOOM
        x1 = (xmax / 1000.0) * img_width / RENDER_ZOOM
        y1 = (ymax / 1000.0) * img_height / RENDER_ZOOM
        scaled.append(entity.model_copy(update={"box": (x0, y0, x1, y1), "name": name}))
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
                VlmEntity(entity_type=AssetType.FIRE_EXIT, name=text, box=label.bbox)
            )
        elif upper.startswith("D") and '"' in text:
            entities.append(VlmEntity(entity_type=AssetType.DOOR, name=text, box=label.bbox))
    return VlmPage(entities=entities, scale_text=scale_text)
