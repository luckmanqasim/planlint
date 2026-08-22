"""Spatial region reader: the VLM cites a dimension, grounded against the page text
layer + plausible range. FunctionModel-scripted — no real vision calls."""

from __future__ import annotations

import pymupdf
import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from planlint.ingest.region_reader import read_region
from planlint.models import Parameter

models.ALLOW_MODEL_REQUESTS = False


def _stair_page():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 300), 'TREAD 7 1/2"', fontsize=8)
    page.insert_text((100, 320), 'RISER 7"', fontsize=8)
    return doc, page


def _model(args):
    return FunctionModel(lambda m, i: ModelResponse(parts=[ToolCallPart(i.output_tools[0].name, args)]))


async def test_read_region_grounds_cited_dims():
    doc, page = _stair_page()
    args = {"measurements": {"tread depth": '7 1/2"', "riser height": '7"'}}
    got = await read_region(
        page, (80, 280, 320, 340),
        {Parameter.TREAD_DEPTH, Parameter.RISER_HEIGHT},
        page.get_text(), _model(args),
    )
    doc.close()
    assert got == {Parameter.TREAD_DEPTH: 7.5, Parameter.RISER_HEIGHT: 7.0}


async def test_read_region_rejects_unprinted_value():
    doc, page = _stair_page()
    args = {"measurements": {"tread depth": '11"'}}  # 11" is not printed on the page
    with pytest.raises(Exception):
        await read_region(
            page, (80, 280, 320, 340), {Parameter.TREAD_DEPTH}, page.get_text(), _model(args)
        )
    doc.close()
