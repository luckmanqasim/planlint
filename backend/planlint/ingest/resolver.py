"""Transitive reference resolver: follow a callout chain and harvest each node.

An asset's plan callout (`1/A3.2`) points at a detail whose region itself references
further details ("PLAN 3/A1.7, ELEVATION 6/A4.1, SECTION 5/A4.0"), where the actual
dimension may live. This does a bounded BFS over that chain: at each detail it reads
the region's grounded dimensions (`region_reader`) for the origin asset and discovers
nested callouts (`detect_text_references`) to follow. Dedup-guarded per (asset, sheet,
detail) with a hard node cap; per-sheet detail detection and per-(sheet, detail, type)
region reads are cached so the VLM isn't re-run. Real-mode only.
"""

from __future__ import annotations

from collections import deque

import pymupdf

from planlint.ingest import vector_geometry as geometry
from planlint.ingest.details import detect_details, region_notes
from planlint.ingest.references import detect_text_references
from planlint.ingest.region_reader import read_region
from planlint.ingest.vlm import RENDER_ZOOM
from planlint.models import AssetType, Detail, Parameter, PhysicalAsset

MAX_NODES = 12  # hard cap on details resolved per document (cost guard)
MAX_DEPTH = 4   # hops from the plan callout


async def resolve_reference_chain(
    doc,
    model,
    page_of_number: dict[str, int],
    registry: dict[str, str],
    seeds: list[tuple[PhysicalAsset, str, str, str]],  # (asset, sheet, detail_num, kind)
    harvest_params: dict[AssetType, set[Parameter]],
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> tuple[list[Detail], dict[str, dict[Parameter, float]]]:
    """Returns (details, asset_fills). `details` are the resolved chain nodes (with
    grounded region measurements); `asset_fills[asset_id]` are the missing params to
    write onto that asset. Nothing is guessed — a value must survive `read_region`'s
    text-layer + range grounding to appear."""
    details: list[Detail] = []
    asset_fills: dict[str, dict[Parameter, float]] = {}
    visited: set[tuple[str, str, str]] = set()  # (asset_id, sheet, detail_num)
    sheet_cache: dict[str, tuple] = {}  # sheet -> (vdetails, labels, text, page)
    read_cache: dict[tuple[str, str, str], dict[Parameter, float]] = {}

    async def sheet_ctx(sheet_number: str):
        if sheet_number not in sheet_cache:
            page = doc[page_of_number[sheet_number]]
            _prims, labels = geometry.extract_primitives(page)
            text = page.get_text()
            png = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)).tobytes("png")
            try:
                vdetails = await detect_details(png, model)
            except Exception:
                vdetails = []
            sheet_cache[sheet_number] = (vdetails, labels, text, page)
        return sheet_cache[sheet_number]

    queue: deque = deque((a, s, d, k, 0, None, None) for (a, s, d, k) in seeds)
    while queue and len(details) < max_nodes:
        asset, sheet, detail_num, kind, depth, parent_sheet, parent_number = queue.popleft()
        if depth > max_depth or sheet not in page_of_number:
            continue
        key = (asset.id, sheet, detail_num)
        if key in visited:
            continue
        visited.add(key)

        vdetails, labels, text, page = await sheet_ctx(sheet)
        vd = next((c for c in vdetails if c.number.strip() == detail_num.strip()), None)
        if vd is None:
            continue
        region = tuple(vd.box)

        want = harvest_params.get(asset.type, set())
        rkey = (sheet, detail_num, asset.type.value)
        if want:
            if rkey not in read_cache:
                try:
                    read_cache[rkey] = await read_region(page, region, want, text, model)
                except Exception:
                    read_cache[rkey] = {}
            harvested = read_cache[rkey]
        else:
            harvested = {}

        details.append(
            Detail(
                sheet_number=sheet,
                number=vd.number.strip(),
                title=vd.title,
                bbox=region,
                kind=kind,
                measurements=dict(harvested),
                notes=region_notes(labels, region),
                depth=depth,
                parent_sheet=parent_sheet,
                parent_number=parent_number,
                source_asset_id=asset.id,
            )
        )
        fills = asset_fills.setdefault(asset.id, {})
        for param, value in harvested.items():
            if param not in asset.measurements and param not in fills:
                fills[param] = value

        # nested callouts inside this detail's region → follow them for this asset,
        # recording this detail (by sheet+number) as their parent in the chain.
        this_number = vd.number.strip()
        for nref in detect_text_references(page, registry, region):
            queue.append(
                (asset, nref.target_sheet_number, nref.detail_num, nref.kind,
                 depth + 1, sheet, this_number)
            )
    return details, asset_fills
