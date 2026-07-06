"""Pure clause-tree builder: text blocks → RegulationClause hierarchy.

Clause ids like '404.2.3' encode their own ancestry — the tree is built from
id prefixes, so the same builder serves both the Docling path and the simple
PyMuPDF text path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from planlint.models import BBox, RegulationClause

_CLAUSE_START = re.compile(r"^(?P<id>\d+(?:\.\d+)*)\s+(?P<rest>\S.*)$", re.DOTALL)
# Title runs up to the first period that ends a word (not a numbering dot).
_TITLE = re.compile(r"^(?P<title>[^.]{1,80}?)\.(?:\s|$)")


@dataclass(frozen=True)
class TextBlock:
    text: str
    page: int
    bbox: BBox | None = None


def _parent_id(clause_id: str, existing: dict[str, RegulationClause]) -> str | None:
    """Longest existing dotted prefix, e.g. 404.2.3 -> 404.2 (or 404 if 404.2 missing)."""
    parts = clause_id.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in existing:
            return candidate
    return None


def build_clause_tree(text_blocks: list[TextBlock]) -> list[RegulationClause]:
    """Group text blocks into clauses keyed by dotted numbering.

    Blocks that don't start a clause (exceptions, advisories, continuation
    paragraphs) are appended to the most recent clause's text. Leading
    material before the first clause is dropped.
    """
    by_id: dict[str, RegulationClause] = {}
    order: list[str] = []
    current: RegulationClause | None = None

    for block in text_blocks:
        text = block.text.strip()
        if not text:
            continue
        match = _CLAUSE_START.match(text)
        # A dotless "clause id" longer than 3 digits is a year or a page
        # number, not a section (real sections look like "404", "404.2.3").
        if match and "." not in match.group("id") and len(match.group("id")) > 3:
            match = None
        if match:
            clause_id = match.group("id")
            rest = match.group("rest").strip()
            title_match = _TITLE.match(rest)
            title = title_match.group("title").strip() if title_match else ""
            parent = _parent_id(clause_id, by_id)
            breadcrumb_parts: list[str] = []
            ancestor = parent
            while ancestor is not None:
                node = by_id[ancestor]
                breadcrumb_parts.insert(0, node.text.splitlines()[0][:80])
                ancestor = node.parent_clause_id
            clause = RegulationClause(
                clause_id=clause_id,
                title=title,
                hierarchy_path=" › ".join(breadcrumb_parts),
                text=text,
                page=block.page,
                bbox=block.bbox,
                parent_clause_id=parent,
            )
            by_id[clause_id] = clause
            order.append(clause_id)
            current = clause
        elif current is not None:
            current = current.model_copy(update={"text": current.text + "\n" + text})
            by_id[current.clause_id] = current

    return [by_id[cid] for cid in order]
