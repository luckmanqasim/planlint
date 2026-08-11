"""Generate the bundled codebook: an excerpt of the 2010 ADA Standards for
Accessible Design (a US-government work, public domain — safe to bundle,
unlike NFPA/ICC codebooks).

Headings are set in bold like a real codebook so layout-aware parsers
(Docling) classify them as section headers; each clause body is its own
paragraph so the simple parser recovers the tree too.

Run:  uv run --project ../backend python generate_ada_excerpt.py
"""

from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "ada_excerpt.pdf"

# (heading, body) — an empty body is a bare heading.
PAGE1 = [
    ("2010 ADA Standards for Accessible Design (excerpt)", ""),
    ("Chapter 4: Accessible Routes", ""),
    ("403 Walking Surfaces", ""),
    (
        "403.1 General.",
        "Walking surfaces that are a part of an accessible route shall comply with 403.",
    ),
    (
        "403.5 Clearances.",
        "Walking surfaces shall provide clearances complying with 403.5.",
    ),
    (
        "403.5.1 Clear Width.",
        "Except as provided in 403.5.2 and 403.5.3, the clear width of walking "
        "surfaces shall be 36 inches (915 mm) minimum.",
    ),
]

PAGE2 = [
    ("404 Doors, Doorways, and Gates", ""),
    (
        "404.1 General.",
        "Doors, doorways, and gates that are part of an accessible route shall "
        "comply with 404.",
    ),
    (
        "404.2 Manual Doors, Doorways, and Manual Gates.",
        "Manual doors and doorways and manual gates intended for user passage "
        "shall comply with 404.2.",
    ),
    (
        "404.2.3 Clear Width.",
        "Door openings shall provide a clear width of 32 inches (815 mm) minimum. "
        "Clear openings of doorways with swinging doors shall be measured between "
        "the face of the door and the stop, with the door open 90 degrees.",
    ),
    (
        "404.2.5 Thresholds.",
        "Thresholds, if provided at doorways, shall be 1/2 inch (13 mm) high maximum.",
    ),
    (
        "404.2.9 Door and Gate Opening Force.",
        "Fire doors shall have a minimum opening force allowable by the appropriate "
        "administrative authority. The force for pushing or pulling open a door or "
        "gate other than fire doors shall be 5 pounds (22.2 N) maximum for interior "
        "hinged doors and gates.",
    ),
]


def write_page(doc: pymupdf.Document, entries: list[tuple[str, str]]) -> None:
    page = doc.new_page(width=612, height=792)
    y = 72.0
    for heading, body in entries:
        rect = pymupdf.Rect(72, y, 540, y + 40)
        used = page.insert_textbox(
            rect, heading, fontsize=12, fontname="hebo", lineheight=1.3
        )
        y += (40 - used) + 6
        if body:
            rect = pymupdf.Rect(72, y, 540, y + 110)
            used = page.insert_textbox(rect, body, fontsize=10, lineheight=1.3)
            y += (110 - used) + 12
        else:
            y += 4


def main() -> None:
    doc = pymupdf.open()
    write_page(doc, PAGE1)
    write_page(doc, PAGE2)
    doc.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
