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

from planlint.ingest.clause_tree import (
    StructuredItem,
    TextBlock,
    build_clause_tree,
    build_clause_tree_from_structure,
)
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


def _docling_items(pdf_path: Path) -> list[StructuredItem]:
    """Layout-aware parse: Docling decides reading order, what is a section
    header, what is a table, and what is page furniture (headers/footers are
    excluded from the body it iterates). We keep that structure instead of
    flattening back to text and re-guessing with regexes."""
    from docling.document_converter import DocumentConverter  # lazy: heavy import
    from docling_core.types.doc import DocItemLabel, TableItem

    document = DocumentConverter().convert(str(pdf_path)).document

    def provenance(item) -> tuple[int, tuple | None]:
        prov = getattr(item, "prov", None)
        if not prov:
            return 0, None
        page_no = prov[0].page_no
        b = prov[0].bbox
        page_height = document.pages[page_no].size.height
        # Docling uses bottom-left origin; flip to top-left PDF points.
        return max(page_no - 1, 0), (b.l, page_height - b.t, b.r, page_height - b.b)

    items: list[StructuredItem] = []
    for item, _level in document.iterate_items():
        label = getattr(item, "label", None)
        if label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            continue  # page numbers ('10-95') and running titles are not content
        page, bbox = provenance(item)
        if isinstance(item, TableItem):
            try:
                table_text = item.export_to_markdown(doc=document)
            except TypeError:  # older docling-core signature
                table_text = item.export_to_markdown()
            if table_text.strip():
                items.append(StructuredItem("table", table_text.strip(), page, bbox))
            continue
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        kind = "header" if label in (DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE) else "text"
        items.append(StructuredItem(kind, text, page, bbox))
    return items


def _docling_available() -> bool:
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


def parse_codebook(pdf_path: Path, mode: str = "auto") -> list[RegulationClause]:
    """Parse a codebook PDF into a clause tree. mode: auto | docling | simple."""
    if mode == "docling" or (mode == "auto" and _docling_available()):
        items = _docling_items(pdf_path)
        clauses = build_clause_tree_from_structure(items)
        if clauses:
            return clauses
        # The layout model found no section headers (unusual scan/layout):
        # fall back to the regex builder over the same reading-ordered text.
        blocks = [TextBlock(text=i.text, page=i.page, bbox=i.bbox) for i in items]
        return build_clause_tree(blocks)
    return build_clause_tree(_simple_blocks(pdf_path))
