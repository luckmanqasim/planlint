"""Spatial ingestion: floor plan PDF → Sheet + PhysicalAsset nodes.

Hybrid strategy per page:
- vector PDFs: deterministic geometry from PyMuPDF; the VLM only classifies,
  its boxes are snapped to the underlying vector primitives.
- raster PDFs: pure VLM detection, flagged `vlm-only` with lower confidence.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Awaitable, Callable

import pymupdf

from planlint.config import settings
from planlint.ingest import vector_geometry as geometry
from planlint.ingest.vlm import RENDER_ZOOM, VlmPage, detect_page, fake_detect_from_labels
from planlint.models import PhysicalAsset, RunEvent

EmitFn = Callable[[RunEvent], Awaitable[None]]


async def _noop_emit(_: RunEvent) -> None:
    return None


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
    with pymupdf.open(pdf_path) as doc:
        total = len(doc)
        for page_index, page in enumerate(doc):
            page_type = geometry.detect_pdf_type(page)
            pdf_type = pdf_type or page_type
            primitives, labels = geometry.extract_primitives(page)

            if settings.planlint_fake_llm:
                vlm_page: VlmPage = fake_detect_from_labels(labels)
            else:
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM))
                vlm_page = await detect_page(pixmap.tobytes("png"), model)

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

            assets: list[PhysicalAsset] = []
            for entity in vlm_page.entities:
                if page_type == "vector":
                    bbox, snapped = geometry.snap_box(entity.box, primitives)
                    source = "vector-snapped" if snapped else "vlm-only"
                    confidence = 0.95 if snapped else 0.6
                else:
                    bbox, source, confidence = entity.box, "vlm-only", 0.6
                measured = geometry.measure_asset(bbox, primitives, labels, scale)
                measurements = {}
                if measured is not None:
                    measurements, from_label = measured
                    if not from_label:
                        confidence = min(confidence, 0.6)  # geometry heuristic
                assets.append(
                    PhysicalAsset(
                        type=entity.entity_type,
                        label=entity.label,
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
    await repo.mark_ingested(document_id, pdf_type)
    return pdf_type
