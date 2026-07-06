# PlanLint

**Spatio-semantic graph linking for AEC compliance.** PlanLint reads your floor
plans to find the physical objects, reads your codebooks to find the legal
constraints, and verifies one against the other — writing every verdict as a
deterministic edge in a Neo4j graph:

```
(:PhysicalAsset {id: "D2", type: "door"})
    -[:VIOLATES {measured: 30, required: ">= 32 in",
                 reason: "clear_width is 30 inches, code requires 32 inches minimum"}]->
(:Regulation {clause: "ADA 404.2.3"})
```

A dual-pane web viewer renders the plan with green/red/amber overlays; click a
red box and the exact failing clause scrolls into view beside it.

## How it works

```
 floor plan PDF ──► spatial ingestion ──► (:PhysicalAsset) ─┐
   PyMuPDF vector geometry + VLM classification,            │   4-stage verification
   boxes snapped to exact CAD geometry                      ├─► Code Hunter (vector search + clause ancestry)
                                                            │   Rule Extractor (LLM → typed Constraint, validated)
 codebook PDF ───► semantic ingestion ───► (:Regulation) ───┘   Deterministic checker (pure Python decides)
   Docling / clause-tree parser, hierarchy preserved,           Compiler (Cypher MERGE, idempotent per run)
   embeddings in Neo4j's native vector index
```

Design principles:

- **No LLM ever decides compliance.** LLMs extract structure (entities from
  drawings, constraints from prose); a pure-Python checker does the comparison.
  Constraint extraction is guarded by validators that bounce implausible values
  back to the model (`ModelRetry`) before they can enter the pipeline.
- **The graph is the cache.** Constraints are first-class nodes: re-verifying a
  revised plan against an unchanged codebook re-runs zero extractions.
- **Honest verdicts.** Anything not machine-checkable (qualitative clauses,
  missing measurements, undetected drawing scale) becomes `NEEDS_REVIEW`, never
  a silent pass.

## Quickstart (5 minutes)

```bash
git clone <this repo> && cd planlint
cp .env.example .env        # add your GOOGLE_API_KEY (or OpenAI/Anthropic — see .env)
docker compose up --build
```

Open http://localhost:3000, click **Load sample project**, then **Run
verification**. The bundled sample is a vector floor plan with four doors —
one of which (D2, 30″) violates the ADA 404.2.3 clear-width minimum — plus a
public-domain excerpt of the 2010 ADA Standards.

No API key yet? Set `PLANLINT_FAKE_LLM=1` in `.env` and the sample runs fully
offline with deterministic extraction (demo/dev only).

## Bring your own codebook

NFPA and ICC/IBC codebooks are copyrighted and are **not** bundled. Upload any
codebook PDF you are licensed to use — the ingestion pipeline is
codebook-agnostic. The 2010 ADA Standards are a US-government work (public
domain), which is why they ship as the working sample.

For real codebooks (multi-column layouts, nested tables), install the Docling
parser backend:

```bash
cd backend && uv sync --extra docling
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PLANLINT_VISION_MODEL` | `google:gemini-3-flash-preview` | VLM for entity classification (any Pydantic AI model string) |
| `PLANLINT_TEXT_MODEL` | `google:gemini-3-flash-preview` | Constraint extraction model |
| `PLANLINT_SEMANTIC_PARSER` | `auto` | `docling` \| `simple` \| `auto` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | localhost | Graph database |
| `PLANLINT_FAKE_LLM` | `0` | Deterministic offline mode (demo/dev only) |

Embeddings are local (fastembed ONNX, 384-dim) — no API cost, works offline.

## Development

```bash
cd backend
uv sync
uv run pytest                  # unit + agent tests: no LLM calls, no database needed
docker compose up -d neo4j
uv run pytest -m integration   # graph round-trip tests against live Neo4j
uv run uvicorn planlint.api.main:app --reload

cd ../frontend
npm install && npm run dev     # http://localhost:3000
```

Agent tests use Pydantic AI's `TestModel`/`FunctionModel` (zero API calls) and
`inline_snapshot` for golden verdict-set regression — snapshots assert the
structured verdict set, never LLM prose, so a diff means behavior changed, not
sampling noise. Accept intended changes with `pytest --inline-snapshot=fix`.

Regenerate the bundled sample PDFs:

```bash
cd samples
uv run --project ../backend python generate_floorplan.py
uv run --project ../backend python generate_ada_excerpt.py
```

## Graph schema

```
(:Project)-[:HAS_DOCUMENT]->(:Document)-[:HAS_SHEET]->(:Sheet)-[:CONTAINS]->(:PhysicalAsset)
(:Document)-[:HAS_CLAUSE]->(:Regulation)-[:PARENT_OF]->(:Regulation)   // clause hierarchy
(:Regulation)-[:DEFINES]->(:Constraint)                                // cached extraction
(:PhysicalAsset)-[:COMPLIES_WITH|VIOLATES|NEEDS_REVIEW {run_id, measured, required, reason}]->(:Regulation)
```

Verdict edges are `MERGE`d on `(asset, regulation, run_id)` — re-runs are
idempotent and prior runs remain queryable as audit history. Explore at
http://localhost:7474 (Neo4j Browser).

## License

Apache-2.0. The bundled ADA excerpt is a US-government work (public domain).
This tool assists compliance review; it does not replace a licensed
professional's judgment or a municipal authority's approval.
