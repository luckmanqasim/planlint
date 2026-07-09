"""Spatial ingestion: floor plan PDF → Sheet + PhysicalAsset nodes.

Hybrid strategy per page:
- vector PDFs: deterministic geometry from PyMuPDF; the VLM only classifies,
  its boxes are snapped to the underlying vector primitives.
- raster PDFs: pure VLM detection, flagged `vlm-only` with lower confidence.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Awaitable, Callable

import pymupdf

from planlint.config import settings
from planlint.ingest import raster_geometry
from planlint.ingest import vector_geometry as geometry
from planlint.ingest.vlm import RENDER_ZOOM, VlmEntity, VlmPage, detect_page, fake_detect_from_labels
from planlint.models import AssetType, Parameter, PhysicalAsset, RunEvent

EmitFn = Callable[[RunEvent], Awaitable[None]]

_OPENING_TYPES = (AssetType.DOOR, AssetType.FIRE_EXIT, AssetType.WINDOW)
_SPACE_TYPES = (AssetType.ROOM, AssetType.CORRIDOR)

_ocr_engine = None  # lazy singleton; model load is expensive


async def _noop_emit(_: RunEvent) -> None:
    return None


def _analyze_page(page) -> tuple[str, list, list, bytes | None]:
    """The sync CPU-bound share of one page — geometry extraction and (when a
    real VLM will be called) rasterization — bundled into a single thread hop
    so the event loop stays responsive during ingestion."""
    page_type = geometry.detect_pdf_type(page)
    primitives, labels = geometry.extract_primitives(page)
    png: bytes | None = None
    if not settings.planlint_fake_llm:
        png = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)).tobytes("png")
    return page_type, primitives, labels, png


def _ocr_labels(png: bytes) -> list[geometry.TextLabel]:
    """Text printed on a scanned page, via RapidOCR when installed (it ships
    with the docling extra). Feeds the same label-based measurement path the
    vector pipeline uses; dimension strings the parser can't read are simply
    ignored downstream."""
    global _ocr_engine
    try:
        if _ocr_engine is None:
            from rapidocr import RapidOCR  # lazy: heavy import

            _ocr_engine = RapidOCR()
    except Exception:
        return []
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        result = _ocr_engine(image)
    except Exception:
        return []
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    if boxes is None or texts is None:
        return []
    labels: list[geometry.TextLabel] = []
    for quad, text in zip(boxes, texts):
        xs = [float(p[0]) for p in quad]
        ys = [float(p[1]) for p in quad]
        labels.append(
            geometry.TextLabel(
                text=str(text),
                bbox=(
                    min(xs) / RENDER_ZOOM,
                    min(ys) / RENDER_ZOOM,
                    max(xs) / RENDER_ZOOM,
                    max(ys) / RENDER_ZOOM,
                ),
            )
        )
    return labels


def _snap_raster_entities(
    analysis: raster_geometry.RasterAnalysis | None,
    entities: list[VlmEntity],
) -> dict[int, tuple]:
    """Deterministically refine VLM boxes against the scan's wall mask.

    Openings are judged first; their (snapped or claimed) boxes plug the wall
    barrier so room flood-fills cannot escape through them. Returns
    {entity_index: (bbox, width_pt | None, fill_area_pt2 | None)} for every
    entity the pixels could confirm. Sync and CPU-bound — call via to_thread."""
    if analysis is None:
        return {}
    results: dict[int, tuple] = {}
    plugs: list = []
    for index, entity in enumerate(entities):
        if entity.entity_type not in _OPENING_TYPES:
            continue
        outcome = raster_geometry.classify_opening(analysis, entity.box)
        if outcome.kind == "snapped":
            results[index] = (outcome.bbox, outcome.width_pt, None)
            plugs.append(outcome.bbox)
        else:
            # "blocked" is informational only: dense window symbols and wall
            # piers are indistinguishable here, so the claim stands as-is.
            plugs.append(entity.box)
    space_centers = {
        index: (
            (entity.box[0] + entity.box[2]) / 2.0,
            (entity.box[1] + entity.box[3]) / 2.0,
        )
        for index, entity in enumerate(entities)
        if entity.entity_type in _SPACE_TYPES
    }
    for index, entity in enumerate(entities):
        if entity.entity_type not in _SPACE_TYPES:
            continue
        others = [c for i, c in space_centers.items() if i != index]
        snapped = raster_geometry.snap_room(
            analysis, entity.box, plugs=plugs, exclude_points_pt=others
        )
        if snapped is not None:
            results[index] = (snapped[0], None, snapped[1])
    return results


async def ingest_floorplan(
    pdf_path: Path,
    document: dict,
    repo,
    model,
    emit: EmitFn = _noop_emit,
) -> str | None:
    """Ingest every page of a floor plan. Returns the detected pdf_type.

    `document` is the graph row; a `manual_scale_text` property (set via the
    scale-entry API when detection fails) overrides anything on the drawing.
    """
    document_id = document["id"]
    manual_scale_text = document.get("manual_scale_text")
    pdf_type: str | None = None
    doc = await asyncio.to_thread(pymupdf.open, pdf_path)
    try:
        total = len(doc)
        for page_index in range(total):
            page = doc[page_index]
            page_type, primitives, labels, png = await asyncio.to_thread(_analyze_page, page)
            pdf_type = pdf_type or page_type

            if settings.planlint_fake_llm:
                vlm_page: VlmPage = fake_detect_from_labels(labels)
            else:
                vlm_page = await detect_page(png, model)

            # Scale priority: manual override > deterministic text scan > VLM.
            scale_text = manual_scale_text or next(
                (label.text for label in labels if "SCALE" in label.text.upper()),
                vlm_page.scale_text,
            )
            scale = geometry.parse_scale(scale_text or "")
            if scale is None:
                await emit(
                    RunEvent(
                        stage="ingest:spatial",
                        message=f"Page {page_index + 1}: drawing scale not detected — "
                        "dimensional checks will need review",
                        level="warning",
                    )
                )

            # Raster pages get the OpenCV treatment: wall mask + OCR text in
            # one thread hop, then per-entity snapping in another.
            raster = None
            raster_snaps: dict[int, tuple] = {}
            if page_type == "raster" and png is not None:
                raster = await asyncio.to_thread(
                    raster_geometry.analyze_page_image, png, RENDER_ZOOM
                )
                labels = labels + await asyncio.to_thread(_ocr_labels, png)
                raster_snaps = await asyncio.to_thread(
                    _snap_raster_entities, raster, vlm_page.entities
                )

            # Vector snapping/measuring is cheap relative to parsing and
            # rasterizing; not worth a thread hop per entity.
            assets: list[PhysicalAsset] = []
            for index, entity in enumerate(vlm_page.entities):
                opening_width_pt: float | None = None
                fill_area_pt2: float | None = None
                if page_type == "vector":
                    bbox, snapped = geometry.snap_box(entity.box, primitives)
                    source = "vector-snapped" if snapped else "vlm-only"
                    confidence = 0.95 if snapped else 0.6
                elif index in raster_snaps:
                    bbox, opening_width_pt, fill_area_pt2 = raster_snaps[index]
                    source, confidence = "raster-snapped", 0.8
                else:
                    bbox, source, confidence = entity.box, "vlm-only", 0.6
                measured = geometry.measure_asset(bbox, primitives, labels, scale)
                measurements = {}
                if measured is not None:
                    measurements, from_label = measured
                    if not from_label:
                        confidence = min(confidence, 0.6)  # geometry heuristic
                if (
                    opening_width_pt is not None
                    and scale is not None
                    and Parameter.CLEAR_WIDTH not in measurements
                ):
                    # Pixel-measured wall-gap width × drawing scale.
                    measurements[Parameter.CLEAR_WIDTH] = round(opening_width_pt * scale, 1)
                if entity.floor_area_m2 is not None and entity.floor_area_m2 > 0:
                    # Printed floor area read off the drawing (m² by plan
                    # convention) — a real dimension, not a geometry guess.
                    measurements[Parameter.AREA] = entity.floor_area_m2
                    if fill_area_pt2 is not None and scale is not None:
                        # Two independent sources: the printed number and the
                        # flood-filled interior × scale². Agreement earns
                        # confidence; disagreement demands review.
                        computed_m2 = raster_geometry.fill_area_to_m2(fill_area_pt2, scale)
                        if raster_geometry.area_agreement(entity.floor_area_m2, computed_m2):
                            confidence = max(confidence, 0.9)
                        else:
                            confidence = min(confidence, 0.5)
                            await emit(
                                RunEvent(
                                    stage="ingest:spatial",
                                    message=f"{entity.name or 'room'}: printed area "
                                    f"{entity.floor_area_m2:g} m² disagrees with the "
                                    f"measured {computed_m2:.1f} m² — check the "
                                    "drawing scale",
                                    level="warning",
                                )
                            )
                assets.append(
                    PhysicalAsset(
                        type=entity.entity_type,
                        label=entity.name,
                        bbox=bbox,
                        confidence=confidence,
                        source=source,
                        measurements=measurements,
                    )
                )

            sheet_id = f"sheet-{uuid.uuid4().hex[:10]}"
            await repo.create_sheet(
                sheet_id=sheet_id,
                document_id=document_id,
                page_number=page_index,
                width=page.rect.width,
                height=page.rect.height,
                scale_text=scale_text,
                scale_in_per_point=scale,
            )
            await repo.upsert_assets(sheet_id, assets)
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message=f"Page {page_index + 1}/{total}: {len(assets)} assets "
                    f"({page_type} pdf)",
                    progress=(page_index + 1) / total,
                )
            )
    finally:
        doc.close()
    await repo.mark_ingested(document_id, pdf_type)
    return pdf_type
