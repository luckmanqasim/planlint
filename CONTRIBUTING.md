# Contributing to PlanLint

Thanks for looking under the hood. This document covers running PlanLint from
source, the test discipline, and the one rule that keeps the design honest. For a
full tour of how the system fits together, read [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Prerequisites

- Python 3.12+ with [`uv`](https://docs.astral.sh/uv/)
- Node 20+
- Docker (for Neo4j, and for the full-stack compose)

## Running from source

```bash
# Backend (from backend/)
cd backend
uv sync                        # base deps
uv sync --extra docling        # optional: the production codebook parser
docker compose up -d neo4j     # graph database (from repo root)
uv run uvicorn planlint.api.main:app --reload --port 8000

# Frontend (from frontend/)
cd ../frontend
npm install && npm run dev     # http://localhost:3000
npm run build                  # also the lint/type gate — run before you call frontend work done
```

Environment lives in `.env` at the repo root (`cp .env.example .env`). For a fully
offline run with no API key, set `PLANLINT_OFFLINE_SAMPLE=1` and use the bundled sample.

## Tests

```bash
cd backend
uv run pytest                              # unit + agent tests: no LLM, no database
uv run pytest tests/test_checker.py        # a single file
docker compose up -d neo4j
uv run pytest -m integration               # graph round-trip tests (needs Neo4j)
uv run pytest -m docling                   # exercises the real Docling parser (slow)

cd ../frontend
npm test                                   # vitest
```

**Test discipline (please preserve it):**

- **No test hits an LLM or a live database by default.** Agent tests use Pydantic
  AI's `TestModel` / `FunctionModel` (zero API calls); graph tests use a
  `FakeRepository`; a `FakeEmbedder` stands in for fastembed. Only `-m integration`
  needs Neo4j.
- **Geometry/schedule/dimension logic is tested against synthetic PDFs and
  primitives** built inline with PyMuPDF — deterministic and offline.
- **Snapshots assert the structured verdict set, never LLM prose**, so a diff means
  behavior changed, not sampling noise. `inline_snapshot` is pinned
  non-interactive; update intentionally with `uv run pytest --inline-snapshot=fix`.
- Don't run `uv sync --all-extras` for the suite — the docling extra flips the
  parser mode and changes assumptions some tests rely on.

## Regenerating the bundled sample PDFs

```bash
cd samples
uv run --project ../backend python generate_floorplan.py
uv run --project ../backend python generate_ada_excerpt.py
```

## Code style

The house-style guide for PlanLint: comments explain *why* not *what*; every
module carries a purpose docstring; Python uses `from __future__ import annotations`
+ full type hints; all Cypher stays parameterized in `graph/repository.py`; blocking
work is offloaded with `asyncio.to_thread`; the frontend uses Tailwind theme tokens only.

## The one rule

**Structure extraction and verdict decision stay separate.** A model may propose
*what* something is; only `verify/checker.py` may decide *whether it complies*, and
only over a value that geometry or printed text has grounded. A change that lets a
model's output flow into a verdict — or into a measurement without grounding — will
be asked to reroute through that seam. It is the whole point of the project.
