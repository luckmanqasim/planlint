"""Domain models. These are the only shapes LLMs are allowed to emit and the
only shapes written to the graph — a malformed model response is a retry,
never a corrupt graph."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class AssetType(str, Enum):
    DOOR = "door"
    FIRE_EXIT = "fire_exit"
    RAMP = "ramp"
    CORRIDOR = "corridor"
    STAIR = "stair"
    WINDOW = "window"
    ROOM = "room"
    OTHER = "other"


class Parameter(str, Enum):
    CLEAR_WIDTH = "clear_width"
    OPENING_HEIGHT = "opening_height"
    THRESHOLD_HEIGHT = "threshold_height"
    MANEUVERING_CLEARANCE = "maneuvering_clearance"
    SLOPE = "slope"
    RISER_HEIGHT = "riser_height"
    TREAD_DEPTH = "tread_depth"
    LANDING_LENGTH = "landing_length"


class Operator(str, Enum):
    MIN = "min"
    MAX = "max"
    RANGE = "range"
    BOOLEAN = "boolean"
    QUALITATIVE = "qualitative"


class VerdictType(str, Enum):
    COMPLIES_WITH = "COMPLIES_WITH"
    VIOLATES = "VIOLATES"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# (x0, y0, x1, y1) in PDF points, origin top-left of the page.
BBox = tuple[float, float, float, float]


class PhysicalAsset(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("asset"))
    type: AssetType
    label: str = ""
    bbox: BBox
    confidence: float = 1.0
    source: Literal["vector-snapped", "vlm-only"] = "vlm-only"
    measurements: dict[Parameter, float] = Field(default_factory=dict)


class RegulationClause(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("reg"))
    clause_id: str  # e.g. "404.2.3"
    title: str = ""
    hierarchy_path: str = ""  # "Chapter 4 › 404 Doors … › 404.2 Manual Doors"
    text: str
    page: int = 0
    bbox: BBox | None = None
    parent_clause_id: str | None = None


class Constraint(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("con"))
    regulation_id: str  # RegulationClause.id it was extracted from
    applies_to: AssetType
    parameter: Parameter | None = None  # None only when operator is qualitative/boolean
    operator: Operator
    value: float | None = None
    value_high: float | None = None  # upper bound when operator == range
    unit: str | None = None
    extraction_confidence: float = 1.0
    summary: str = ""  # human-readable restatement, used in verdict reasons


class CheckResult(BaseModel):
    verdict: VerdictType
    measured: float | None = None  # inches (canonical)
    required: str | None = None  # human string, e.g. ">= 32 in"
    reason: str = ""


class RunEvent(BaseModel):
    stage: str  # "ingest:semantic" | "ingest:spatial" | "verify" | "done" | "error"
    message: str
    progress: float | None = None  # 0..1 within the stage
    level: Literal["info", "warning", "error"] = "info"
    asset_id: str | None = None


class RunSummary(BaseModel):
    run_id: str
    counts: dict[str, int] = Field(default_factory=dict)  # VerdictType value -> count
    errors: list[str] = Field(default_factory=list)
