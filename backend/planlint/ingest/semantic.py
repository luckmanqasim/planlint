"""Semantic ingestion: codebook PDF → RegulationClause list.

Two parser backends feed the same clause-tree builder:

- ``docling`` (recommended for real codebooks): layout-aware parsing that
  survives multi-column pages and nested tables. Heavy install; packaged as
  the ``planlint[docling]`` extra.
- ``simple``: PyMuPDF text blocks. Sufficient for clean single-column text
  PDFs (like the bundled ADA excerpt) and keeps the base install light.

``auto`` uses Docling when importable, else falls back to simple.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from planlint.ingest.clause_tree import TextBlock, build_clause_tree
from planlint.models import RegulationClause


def _simple_blocks(pdf_path: Path) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    with pymupdf.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
                if text.strip():
                    blocks.append(
                        TextBlock(text=text.strip(), page=page_index, bbox=(x0, y0, x1, y1))
                    )
    return blocks


def _docling_blocks(pdf_path: Path) -> list[TextBlock]:
    from docling.document_converter import DocumentConverter  # lazy: heavy import

    result = DocumentConverter().convert(str(pdf_path))
    document = result.document
    blocks: list[TextBlock] = []
    for item, _level in document.iterate_items():
        text = getattr(item, "text", "") or ""
        if not text.strip():
            continue
        page = 0
        bbox = None
        prov = getattr(item, "prov", None)
        if prov:
            page = max(prov[0].page_no - 1, 0)  # docling pages are 1-based
            b = prov[0].bbox
            page_height = document.pages[prov[0].page_no].size.height
            # Docling uses bottom-left origin; flip to top-left PDF points.
            bbox = (b.l, page_height - b.t, b.r, page_height - b.b)
        blocks.append(TextBlock(text=text.strip(), page=page, bbox=bbox))
    return blocks


def _docling_available() -> bool:
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


def parse_codebook(pdf_path: Path, mode: str = "auto") -> list[RegulationClause]:
    """Parse a codebook PDF into a clause tree. mode: auto | docling | simple."""
    if mode == "docling" or (mode == "auto" and _docling_available()):
        blocks = _docling_blocks(pdf_path)
    else:
        blocks = _simple_blocks(pdf_path)
    return build_clause_tree(blocks)
