"""VLM detection agent tests: box/name validators (retry loop) and
pixel→point conversion. FunctionModel-scripted — no real vision calls."""

import pymupdf
import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from planlint.ingest.vlm import detect_page

models.ALLOW_MODEL_REQUESTS = False


def structured(info: AgentInfo, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


def png_100x80() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 80))
    pix.clear_with(255)
    return pix.tobytes("png")


GOOD_ARGS = {
    "entities": [
        {"entity_type": "door", "name": "D1", "box": [0, 0, 500, 500]},
        {
            "entity_type": "room",
            "name": "GARAGE",
            "floor_area_m2": 39.37,
            "box": [100, 100, 700, 800],  # wall-to-wall: 42% of the page
        },
    ],
    "scale_text": None,
}


def scripted_retry_then(good_args: dict, bad_args: dict):
    """Model that emits bad output once, then corrects after a retry prompt."""
    calls = {"n": 0}

    def model_fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return structured(info, bad_args)
        retry_parts = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        assert retry_parts, "expected a RetryPromptPart after ModelRetry"
        return structured(info, good_args)

    return model_fn, calls


async def test_inverted_box_triggers_retry_then_corrects():
    bad = {
        "entities": [
            # ymax < ymin — the malformed shape observed from a real Gemini run.
            {"entity_type": "door", "name": "D1", "box": [512, 508, 90, 543]}
        ],
        "scale_text": None,
    }
    model_fn, calls = scripted_retry_then(GOOD_ARGS, bad)
    page = await detect_page(png_100x80(), FunctionModel(model_fn))
    assert calls["n"] == 2
    # [ymin, xmin, ymax, xmax] on 0-1000 -> pixels -> PDF points (zoom 2).
    assert page.entities[0].box == (0.0, 0.0, 25.0, 20.0)


async def test_label_sized_room_boxes_trigger_retry():
    """A page whose rooms are all boxed around their name text (slivers on
    the 0-1000 grid) must be bounced back for full wall-to-wall extents."""
    bad = {
        "entities": [
            {"entity_type": "room", "name": "GARAGE", "box": [401, 133, 431, 199]},
            {"entity_type": "room", "name": "CUISINE", "box": [360, 300, 385, 350]},
        ],
        "scale_text": None,
    }
    model_fn, calls = scripted_retry_then(GOOD_ARGS, bad)
    page = await detect_page(png_100x80(), FunctionModel(model_fn))
    assert calls["n"] == 2
    assert any(e.entity_type.value == "room" for e in page.entities)


async def test_area_in_name_triggers_retry():
    bad = {
        "entities": [
            {
                "entity_type": "room",
                "name": "GARAGE 39.37 m²",
                "box": [100, 100, 700, 800],
            }
        ],
        "scale_text": None,
    }
    model_fn, calls = scripted_retry_then(GOOD_ARGS, bad)
    page = await detect_page(png_100x80(), FunctionModel(model_fn))
    assert calls["n"] == 2
    room = next(e for e in page.entities if e.entity_type.value == "room")
    assert room.name == "GARAGE"
    assert room.floor_area_m2 == 39.37


async def test_persistent_bad_boxes_raise():
    bad = {
        "entities": [{"entity_type": "door", "name": "D1", "box": [512, 508, 90, 543]}],
        "scale_text": None,
    }

    def model_fn(messages, info):
        return structured(info, bad)

    with pytest.raises(Exception, match="[Ee]xceeded|retries"):
        await detect_page(png_100x80(), FunctionModel(model_fn))
