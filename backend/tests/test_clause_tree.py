"""Tests for the clause tree builder and the simple codebook parser."""

from planlint.ingest.clause_tree import TextBlock, build_clause_tree


def blocks(*texts_pages) -> list[TextBlock]:
    return [
        TextBlock(text=text, page=page, bbox=(0.0, float(i * 20), 100.0, float(i * 20 + 15)))
        for i, (text, page) in enumerate(texts_pages)
    ]


def test_three_level_nesting():
    clauses = build_clause_tree(
        blocks(
            ("404 Doors, Doorways, and Gates", 1),
            ("404.2 Manual Doors. Manual doors shall comply with 404.2.", 1),
            (
                "404.2.3 Clear Width. Door openings shall provide a clear width of "
                "32 inches (815 mm) minimum.",
                1,
            ),
        )
    )
    by_id = {c.clause_id: c for c in clauses}
    assert set(by_id) == {"404", "404.2", "404.2.3"}
    assert by_id["404"].parent_clause_id is None
    assert by_id["404.2"].parent_clause_id == "404"
    assert by_id["404.2.3"].parent_clause_id == "404.2"


def test_hierarchy_path_breadcrumb():
    clauses = build_clause_tree(
        blocks(
            ("404 Doors, Doorways, and Gates", 1),
            ("404.2 Manual Doors.", 1),
            ("404.2.3 Clear Width. 32 inches minimum.", 2),
        )
    )
    deep = next(c for c in clauses if c.clause_id == "404.2.3")
    assert deep.hierarchy_path == "404 Doors, Doorways, and Gates › 404.2 Manual Doors."
    assert deep.page == 2


def test_title_extracted():
    clauses = build_clause_tree(blocks(("404.2.3 Clear Width. Door openings shall...", 1)))
    assert clauses[0].title == "Clear Width"


def test_preamble_attaches_to_previous_clause():
    clauses = build_clause_tree(
        blocks(
            ("404.2.3 Clear Width. Openings shall provide 32 inches minimum.", 1),
            ("EXCEPTION: Door openings within hospital patient rooms.", 1),
        )
    )
    assert len(clauses) == 1
    assert "EXCEPTION" in clauses[0].text


def test_leading_preamble_without_clause_is_dropped():
    clauses = build_clause_tree(
        blocks(
            ("2010 ADA Standards for Accessible Design", 1),
            ("404 Doors, Doorways, and Gates", 1),
        )
    )
    assert [c.clause_id for c in clauses] == ["404"]


def test_gap_in_hierarchy_uses_longest_prefix():
    """404.2.3 with no 404.2 present parents to 404."""
    clauses = build_clause_tree(
        blocks(
            ("404 Doors", 1),
            ("404.2.3 Clear Width. 32 inches minimum.", 1),
        )
    )
    deep = next(c for c in clauses if c.clause_id == "404.2.3")
    assert deep.parent_clause_id == "404"


def test_provenance_preserved():
    clauses = build_clause_tree(blocks(("403.5.1 Clear Width. 36 inches minimum.", 7)))
    c = clauses[0]
    assert c.page == 7
    assert c.bbox is not None
