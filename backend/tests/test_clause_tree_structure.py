"""Tests for the structure-driven clause builder (layout-aware parser path),
plus a real-Docling parse of the bundled ADA sample."""

from pathlib import Path

import pytest

from planlint.ingest.clause_tree import StructuredItem, build_clause_tree_from_structure

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def header(text: str, page: int = 0) -> StructuredItem:
    return StructuredItem(kind="header", text=text, page=page)


def text(content: str, page: int = 0) -> StructuredItem:
    return StructuredItem(kind="text", text=content, page=page)


def table(content: str, page: int = 0) -> StructuredItem:
    return StructuredItem(kind="table", text=content, page=page)


def test_numbered_headers_with_body_and_tables():
    clauses = build_clause_tree_from_structure(
        [
            header("1.1 \nSHORT TITLE"),
            text("These Regulations may be cited as the Regulations."),
            header("1.2 INTERPRETATION"),
            text("Words and phrases shall have the meaning ascribed in Section 2."),
            table("| Lot Area (minimum) | 450 metres square |"),
        ]
    )
    assert [c.clause_id for c in clauses] == ["1.1", "1.2"]
    assert "may be cited" in clauses[0].text
    assert "Lot Area (minimum)" in clauses[1].text  # table attached, not a section


def test_section_word_header_provides_parent():
    clauses = build_clause_tree_from_structure(
        [
            header("SECTION 1 – TITLE AND APPLICATION"),
            header("1.1 SHORT TITLE"),
            text("These Regulations..."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["1", "1.1"]
    assert clauses[0].title == "TITLE AND APPLICATION"
    assert clauses[1].parent_clause_id == "1"


def test_zone_code_header_joins_title_header():
    clauses = build_clause_tree_from_structure(
        [
            header("10.95 ZONE TABLES"),
            header("AG"),
            header("AGRICULTURE (AG) ZONE"),
            text("(1) PERMITTED USES"),
            table("| Accessory Building | Public Use |"),
            header("O"),
            header("OPEN SPACE (O) ZONE"),
            text("(3) ZONE STANDARDS SHALL BE IN THE DISCRETION OF COUNCIL."),
        ]
    )
    ids = [c.clause_id for c in clauses]
    assert ids == ["10.95", "AG", "O"]
    ag = clauses[1]
    assert ag.title == "AGRICULTURE (AG) ZONE"
    assert "PERMITTED USES" in ag.text
    assert "Accessory Building" in ag.text


def test_toc_section_and_dot_leader_headers_are_skipped():
    clauses = build_clause_tree_from_structure(
        [
            header("Table of Contents"),
            text("1.1 SHORT TITLE ................. 1-1"),
            header("1.2 INTERPRETATION .............. 1-1"),  # misclassified TOC line
            header("1.1 SHORT TITLE"),
            text("These Regulations may be cited..."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["1.1"]
    assert clauses[0].page == 0
    assert "may be cited" in clauses[0].text
    assert "1-1" not in clauses[0].text  # TOC entries never attached


def test_addresses_in_body_text_do_not_become_sections():
    clauses = build_clause_tree_from_structure(
        [
            header("10.7 SITE-SPECIFIC AMENDMENTS"),
            text("485 Topsail Road (PID #46960). 52 metres (2024-07-19)"),
            text("1 Bull\n1000 Broiler Chickens or roasters (1.8-2.3 kg each)"),
        ]
    )
    assert [c.clause_id for c in clauses] == ["10.7"]
    assert "485 Topsail Road" in clauses[0].text


def test_paren_numbered_headers_scope_under_their_zone():
    """'(1) PERMITTED USES' repeats in every zone — each must become a child
    of its own zone, never merge across zones."""
    clauses = build_clause_tree_from_structure(
        [
            header("RURAL RESIDENTIAL (RR) ZONE"),
            header("(1) PERMITTED USES"),
            text("Single Detached Dwelling"),
            header("(2) DISCRETIONARY USES"),
            text("Home Occupation"),
            header("RURAL RESIDENTIAL INFILL (RRI) ZONE"),
            header("(1) PERMITTED USES"),
            text("Accessory Building"),
        ]
    )
    assert [c.clause_id for c in clauses] == ["RR", "RR.1", "RR.2", "RRI", "RRI.1"]
    assert clauses[1].parent_clause_id == "RR"
    assert clauses[4].parent_clause_id == "RRI"
    assert "Single Detached" in clauses[1].text
    assert "Accessory Building" in clauses[4].text


def test_duplicate_header_ids_merge():
    clauses = build_clause_tree_from_structure(
        [
            header("7.6 LANDSCAPING AND SCREENING"),
            text("Original requirements."),
            header("7.6 LANDSCAPING AND SCREENING"),
            text("Amended requirements."),
        ]
    )
    assert len(clauses) == 1
    assert "Original requirements" in clauses[0].text
    assert "Amended requirements" in clauses[0].text


def test_no_headers_returns_empty_for_fallback():
    clauses = build_clause_tree_from_structure(
        [text("404.2.3 Clear Width. Door openings shall provide 32 inches minimum.")]
    )
    assert clauses == []


@pytest.mark.docling
def test_docling_parses_ada_sample_end_to_end():
    """Real Docling over the bundled ADA excerpt: the layout-aware path must
    recover the numbered sections the pipeline's golden flow relies on."""
    pytest.importorskip("docling")
    from planlint.ingest.semantic import parse_codebook

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    clauses = parse_codebook(pdf, "docling")
    ids = {c.clause_id for c in clauses}
    assert {"404.2.3", "404.2.5", "404.2.9", "403.5.1"} <= ids
    by_id = {c.clause_id: c for c in clauses}
    assert "32 inches" in by_id["404.2.3"].text
    assert len(ids) == len(clauses)  # no duplicates


@pytest.mark.docling
def test_docling_chunked_parse_matches_unchunked():
    """Chunked conversion (the memory-bounding path for large codebooks) must
    yield the same clause tree as a single-call parse — a chunk boundary can
    never change what gets ingested."""
    pytest.importorskip("docling")
    from planlint.ingest.clause_tree import build_clause_tree_from_structure
    from planlint.ingest.semantic import _docling_items

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    # The sample is 2 pages: chunk_pages=1 forces a chunk boundary mid-document.
    chunked = build_clause_tree_from_structure(_docling_items(pdf, chunk_pages=1))
    whole = build_clause_tree_from_structure(_docling_items(pdf, chunk_pages=10_000))
    assert [(c.clause_id, c.page, c.text) for c in chunked] == [
        (c.clause_id, c.page, c.text) for c in whole
    ]
    # Worker windows concatenate the same way (per-worker page_span slices).
    split = _docling_items(pdf, page_span=(1, 1)) + _docling_items(pdf, page_span=(2, 2))
    windowed = build_clause_tree_from_structure(split)
    assert [(c.clause_id, c.page, c.text) for c in windowed] == [
        (c.clause_id, c.page, c.text) for c in whole
    ]


def test_isolated_simple_parse_stays_in_process():
    """The lightweight simple parser must not pay the subprocess tax — and its
    output must match a direct parse."""
    from planlint.ingest.semantic import parse_codebook, parse_codebook_isolated

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    direct = parse_codebook(pdf, "simple")
    isolated = parse_codebook_isolated(pdf, "simple")
    assert [(c.clause_id, c.text) for c in isolated] == [
        (c.clause_id, c.text) for c in direct
    ]


@pytest.mark.docling
def test_isolated_docling_parse_round_trips_through_subprocess():
    """The Docling path runs in a spawn subprocess (memory isolation): clauses
    must pickle back intact across the process boundary."""
    pytest.importorskip("docling")
    from planlint.ingest.semantic import parse_codebook_isolated

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    clauses = parse_codebook_isolated(pdf, "docling")
    ids = {c.clause_id for c in clauses}
    assert {"404.2.3", "404.2.5", "404.2.9", "403.5.1"} <= ids
