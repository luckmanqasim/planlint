# PlanLint — Architecture & Codebase Reference

A module-by-module tour of how PlanLint works, written to let you reason about
any part of the system without re-reading it from scratch. It is the companion to
the [`README.md`](README.md) (which sells the design); this document explains the
machinery.

**Contents**
1. [System at a glance](#1-system-at-a-glance)
2. [End-to-end lifecycle](#2-end-to-end-lifecycle)
3. [Repository layout](#3-repository-layout)
4. [Domain models — the contract](#4-domain-models--the-contract)
5. [Backend: API layer](#5-backend-api-layer)
6. [Backend: the graph](#6-backend-the-graph)
7. [Backend: spatial ingestion](#7-backend-spatial-ingestion)
8. [Backend: semantic ingestion](#8-backend-semantic-ingestion)
9. [Backend: verification](#9-backend-verification)
10. [The deterministic seam in detail](#10-the-deterministic-seam-in-detail)
11. [Frontend](#11-frontend)
12. [Testing architecture](#12-testing-architecture)
13. [Configuration & runtime](#13-configuration--runtime)
14. [Where to extend](#14-where-to-extend)

---

## 1. System at a glance

Two inputs, one graph, one verdict authority.

```
 floor plan PDF ─► spatial ingestion ─► (:PhysicalAsset)  ┐
                   (ingest/spatial.py + geometry/schedule) │
                                                           ├─► Code Hunter ─► Rule Extractor ─► Checker ─► verdict edges
 codebook PDF  ─► semantic ingestion ─► (:Regulation)     ┘   (verify/*)                        (pure Python)
                   (ingest/semantic.py / llm_parser.py)
                                        │
                            Neo4j graph (graph/repository.py) ◄──────── FastAPI (api/*) ◄──── Next.js UI (frontend/)
```

The **backend** (`backend/planlint/`) is a FastAPI app that ingests PDFs into a
Neo4j graph and runs a verification pipeline. The **frontend** (`frontend/`) is a
Next.js dual-pane reviewer. Everything crosses boundaries as Pydantic models
(`models.py`) and is persisted only through one repository class
(`graph/repository.py`). The load-bearing rule is in every module's design: **the
vision/text models extract structure; the pure-Python checker
(`verify/checker.py`) is the only thing that decides compliance.**

---

## 2. End-to-end lifecycle

A single verification run, traced through the code:

1. **Create/seed a project.** `POST /projects` or `POST /projects/sample`
   (`api/routes.py`) copies the bundled `sample_floorplan.pdf` +
   `ada_excerpt.pdf` into the data dir and registers two `Document` rows.
2. **Upload documents** (`POST /projects/{id}/documents?kind=floorplan|codebook`)
   — the PDF is stored under `data/{project}/` and a `Document` node is created.
   Files are never ingested on upload; ingestion is lazy, at verification time.
3. **Start a run** (`POST /projects/{id}/verify`) — creates a `run_id`, registers
   it with the `RunManager` (`api/runs.py`), and launches `run_full`
   (`verify/pipeline.py`) as a background `asyncio` task. Returns immediately.
4. **`run_full`** → `ingest_pending_documents` → for each un-ingested document:
   - **codebook** → `resolve_parser_mode` picks LLM / Docling / simple; the parser
     yields `RegulationClause`s; `embedder.embed` produces vectors;
     `repo.upsert_clauses` writes the clause tree + embeddings into Neo4j.
   - **floorplan** → `ingest_floorplan` (`ingest/spatial.py`) writes `Sheet` +
     `PhysicalAsset` nodes.
5. **`run_verification`** iterates every asset. For each: `hunt` (Code Hunter)
   retrieves governing clauses → `_constraints_for` extracts/loads typed
   `Constraint`s (**graph is the cache**) → `check` (the checker) produces a
   `CheckResult` → `repo.write_verdict` MERGEs a verdict edge. Per-asset
   `try/except` isolation: one bad asset never kills the run.
6. **Progress streams** the whole time: every `RunEvent` is fanned out over SSE
   (`GET /runs/{run_id}/events`); the frontend's `useVerificationRun` consumes it.
7. **Results** (`GET /projects/{id}/results`) returns sheets, assets, clauses, and
   verdict edges for the latest (or a named) run; the UI renders the two panes.

---

## 3. Repository layout

```
backend/planlint/
  api/            HTTP surface + run/SSE management
    main.py       create_app() factory; lifespan wires Neo4j + embedder
    routes.py     every endpoint (thin; delegates to repo/pipeline)
    runs.py       RunManager: in-memory run state + SSE fan-out
  graph/
    repository.py GraphRepository — every Cypher statement in the system
  ingest/         PDF → graph nodes
    spatial.py        floor plan → Sheet + PhysicalAsset (orchestrator)
    sheet_type.py     classify each sheet by its title block
    vector_geometry.py deterministic geometry from PyMuPDF (snap, scale, gaps, dims)
    dimensions.py     dimension-grid: printed dims grounded on their dimension lines
    schedule.py       door/window schedule → mark→size index
    raster_geometry.py OpenCV wall-mask analysis for scanned pages
    elevation.py      vertical dimensions (stair riser/tread) from elevations/sections
    vlm.py            vision agent: classifies entities on a rendered page
    ocr.py            shared RapidOCR engine (scans)
    semantic.py       codebook → RegulationClause list (parser-mode dispatch)
    clause_tree.py    parsed content → RegulationClause hierarchy
    llm_parser.py     LLM-first per-page codebook transcription (text-layer verified)
    embeddings.py     local fastembed (bge-small, 384-dim)
  verify/         the pipeline and the verdict authority
    pipeline.py       run_full / ingest_pending_documents / run_verification
    code_hunter.py    retrieve governing clauses (vector search + ancestry)
    rule_extractor.py LLM → typed Constraint (validated) — the ONE interpretive LLM
    offline_extractor.py deterministic constraint extraction for offline sample mode
    checker.py        pure Python — the ONLY component that decides compliance
  config.py       environment-driven settings
  models.py       domain models (the only shapes LLMs emit / the graph stores)

frontend/src/
  app/            Next.js routes (home, project workspace)
  components/     the two-pane reviewer + inspectors + overlays
  lib/            HTTP client, types, verdict/asset presentation, run lifecycle
```

---

## 4. Domain models — the contract

`models.py` is the single source of truth for every shape that crosses a boundary
— an LLM response, an API payload, a graph write. A malformed model response is a
retry, never a corrupt graph.

**Enums**

| Enum | Values | Role |
|---|---|---|
| `AssetType` | door, fire_exit, ramp, corridor, stair, window, room, other | kind of physical object |
| `Parameter` | area_m2, clear_width, opening_height, threshold_height, maneuvering_clearance, slope, riser_height, tread_depth, landing_length | the measurable quantity a constraint governs (also the asset measurement key) |
| `Operator` | min, max, range, boolean, qualitative | how a constraint bounds the measurement |
| `VerdictType` | COMPLIES_WITH, VIOLATES, NEEDS_REVIEW | the checker's ruling |

**Models**

- **`PhysicalAsset`** — `type`, `label`, `bbox` (PDF points, top-left origin),
  `confidence`, `source` (`vector-snapped` / `raster-snapped` / `vlm-only` /
  `schedule`), `measurements: dict[Parameter, float]`. This is what spatial
  ingestion produces and what the checker consumes.
- **`RegulationClause`** — `clause_id` ("404.2.3"), `title`, `hierarchy_path`
  ("Chapter 4 › 404 Doors › 404.2 Manual Doors"), `text`, `page`, `bbox`,
  `parent_clause_id`. The unit of the codebook tree.
- **`Constraint`** — `applies_to` (AssetType), `parameter`, `operator`, `value`,
  `value_high` (for range), `unit`, `summary`. The machine-checkable rule
  extracted from a clause; cached as a graph node.
- **`CheckResult`** — `verdict`, `measured` (inches, canonical), `required`
  (human string), `reason`. The checker's output; becomes the verdict edge's
  properties.
- **`RunEvent`** — `stage`, `message`, `progress`, `level`, `asset_id`. One SSE
  progress update.
- **`RunSummary`** — `run_id`, `counts` (per verdict), `errors`.

Canonical units: lengths compare in **inches**, areas in **square metres**
(`area_m2`), slope as a **grade fraction** (rise/run).

---

## 5. Backend: API layer

### `api/main.py` — the factory
`create_app(repo, embedder)` builds the FastAPI app and stashes `repo`,
`embedder`, and a `RunManager` on `app.state`. In production the `lifespan` wires
the real Neo4j `AsyncDriver` and a fastembed embedder and calls
`repo.init_schema()`; tests pass a `FakeRepository`/`FakeEmbedder` instead. This
factory is the primary test seam.

### `api/routes.py` — the HTTP surface
Thin handlers; each delegates to the repository or the pipeline. Project ids are
regex-validated (they become directory names) and upload paths are confined to the
data dir.

| Method & path | Purpose |
|---|---|
| `GET /projects` | list projects + document counts |
| `POST /projects` | create empty project |
| `POST /projects/sample` | seed the bundled ADA door-clearance sample |
| `DELETE /projects/{id}` | cascade-delete graph, evict runs, remove files |
| `POST /projects/{id}/documents?kind=` | upload a floorplan/codebook PDF |
| `DELETE /documents/{id}` | delete one document (cascades) |
| `POST /projects/{id}/verify` | start a background run → `run_id` |
| `GET /runs/{run_id}/events` | **SSE** stream of `RunEvent`s (history + live) |
| `GET /projects/{id}/results?run_id=` | sheets/assets/clauses/verdicts for a run |
| `GET /documents/{id}/pdf` | serve original PDF for the viewer |
| `PATCH /documents/{id}/scale` | set a manual drawing scale (overrides detection) |

### `api/runs.py` — `RunManager`
Holds in-memory run state with a cap + TTL eviction. Each run buffers its
`RunEvent` history and a set of live subscribers. `subscribe(run_id)` **replays
the full history first**, then yields live events — so a client that connects late
(or reconnects) still sees the whole run. `drop_project` evicts a deleted
project's runs. Purely in-memory: run *events* are ephemeral; run *verdicts* live
in the graph.

---

## 6. Backend: the graph

### `graph/repository.py` — `GraphRepository`
**Every Cypher statement in the system lives here**, all parameterized. Nothing
else in the codebase talks to Neo4j.

**Schema** (`init_schema` creates constraints + the native vector index):

```
(:Project)-[:HAS_DOCUMENT]->(:Document)-[:HAS_SHEET]->(:Sheet)-[:CONTAINS]->(:PhysicalAsset)
(:Document)-[:HAS_CLAUSE]->(:Regulation)-[:PARENT_OF]->(:Regulation)   // clause hierarchy
(:Regulation)-[:DEFINES]->(:Constraint)                                // cached extraction
(:PhysicalAsset)-[:COMPLIES_WITH|VIOLATES|NEEDS_REVIEW {run_id, measured, required, reason}]->(:Regulation)
```

**Method groups**
- Projects/documents: `create_project`, `get_project`, `delete_project`,
  `list_projects`, `create_document`, `get_documents`, `delete_document`,
  `set_document_manual_scale`, `mark_ingested`.
- Sheets/assets: `create_sheet`, `set_sheet_scale`, `get_sheet`, `upsert_assets`,
  `get_assets`.
- Clauses: `upsert_clauses` (writes the tree + embeddings), `get_clauses`,
  `vector_search` (native vector index over clause embeddings), `ancestors`
  (walk `PARENT_OF` up).
- Constraints (**the cache**): `get_constraints`, `save_constraints`,
  `mark_constraints_extracted`, `constraints_extracted`.
- Verdicts/results: `write_verdict`, `latest_run_id`, `results_payload`.

**Two design properties to keep:**
- **Idempotent runs.** `write_verdict` MERGEs on `(asset, regulation, run_id)`, so
  re-running is safe and prior runs remain as queryable audit history.
- **The graph is the cache.** `constraints_extracted` + `get_constraints` mean a
  re-verification against an unchanged codebook re-runs **zero** LLM extractions —
  the expensive step happens once per clause, ever.

---

## 7. Backend: spatial ingestion

Turns a (possibly multi-sheet) floor-plan PDF into `PhysicalAsset` nodes. This is
the largest and most heuristic subsystem — and where the honest limitations live.

### `ingest/spatial.py` — the orchestrator
`ingest_floorplan(pdf, document, repo, model, emit)`:
1. **Document-level pass first**: classify every page once (`sheet_type.py`), then
   build one **schedule index** (`schedule.parse_schedule_index`) merged across all
   schedule sheets, so an opening's callout can be joined to its size regardless of
   which sheet the schedule is on.
2. **Per page** (`process_page`, isolated in try/except): route by sheet type —
   floor plans/`OTHER` run the plan detector, `SCHEDULE` sheets feed only the
   mark→size index (no standalone assets — a schedule row has no location on a
   plan), elevations/sections run the vertical-dimension detector, and skip-types
   (foundation, roof, RCP/electrical, detail, cover) are recorded but not linted.
3. **Per detected entity** (vector path): snap the box to geometry, then resolve
   its measurements by a strict **precedence** (see §10), attach `source` +
   `confidence`, and keep the asset only if it carries something checkable.

Key helpers here: `_resolve_mark` / `_resolve_callout` (join an opening to a
schedule mark — VLM read, else nearest matching callout label, kind-restricted +
proximity-bound), `_extent_axis`, `_printed_area_present` (ground a room's area
against the text layer), `_analyze_page` (PyMuPDF primitives/labels + render).

### `ingest/sheet_type.py`
Deterministic, **title-driven** classification. Reads the largest title-tier text
lines (a drawing title is typeset larger than the callouts that mention other
sheets) and maps them to a `SheetType`. A real schedule sheet wins over a plain
majority vote. This is what stops the plan detector from running on an elevation.

### `ingest/vector_geometry.py`
Pure math over PyMuPDF primitives — no LLM. The deterministic backbone:
- `extract_primitives` → line segments + text labels; `parse_scale` (real
  inches-per-point from `1/4" = 1'-0"` etc.).
- `parse_dimension_label` — reads a CAD dimension, and **rejects numbers embedded
  in structural/material notes** (`2X12 JOISTS @ 16" OC`).
- `snap_box` / `snap_room_box` — snap a VLM box to the real primitives (rooms snap
  to bounding walls; text-terminated segments excluded so boxes hug walls, not
  annotation).
- `build_wall_runs` + `classify_opening_vector` — grade a claimed opening against
  the wall geometry, tri-state: `snapped` (real flanked gap), `refuted`
  (hatching/fill occupies it → dropped), `unknown` (keep for review). The snapped
  gap must be corroborated (comparable to the VLM box, and plausible in real
  inches given the scale) so a door can't swallow a whole wall; box thickness comes
  from the paired wall faces, and a box that landed beside the opening is
  repositioned onto a single unambiguous nearby gap.
- `measure_asset` — a nearby dimension label, else the longest interior segment ×
  scale (openings only). `parse_slope_label` — ramp grade.

### `ingest/dimensions.py`
The **dimension grid**. `build_dimension_grid` binds each dimension text to the
axis-aligned dimension line it sits on, and keeps it **only when `value ≈ span ×
scale`** — the geometry-grounds-the-number seam. `measure_span` then attributes a
dimension to the asset whose extent its span brackets (a room's outside dimension
line lands on the room). Axis-aligned single-span only; chained sums are the
deferred follow-up.

### `ingest/schedule.py`
`parse_schedule_index(page)` builds a **mark→`OpeningSpec`** index from a schedule
— covering both a **ruled table** PyMuPDF recovers (`_index_rows`, testable without
table detection) and one drawn as **positioned text** (real CAD schedules are
line-art `find_tables` can't recover): the text scan tracks the current
`DOOR SCHEDULE` / `WINDOW SCHEDULE` section, and for each row reads the size
(`parse_size` → `W×H`) and the mark just left of it. The index is the schedule's
only output — it is joined to a real plan opening by callout, never emitted as a
standalone (locationless) asset.

### `ingest/raster_geometry.py`
For scanned pages: OpenCV wall-mask analysis. Flood-fills room interiors (openings
plugged so fills can't leak), snaps opening widths against pixels, and
cross-checks printed vs. computed area (`area_agreement`, `fill_area_to_m2`). The
weakest path — no dimension grid, no schedule text.

### `ingest/elevation.py`
`detect_elevation_page` reads the **vertical** dimensions a plan can't show (stair
riser/tread). The model cites only dimensions printed on the sheet; each is
verified against the page text layer and parsed to inches — never estimated.

### `ingest/vlm.py`
The vision agent (Pydantic AI, temperature 0, `retries=2`, box output-validator).
`VlmEntity` carries `entity_type`, `name`, `floor_area_m2`, `mark` (the schedule
callout), `box`. `detect_page` classifies entities on a rendered page;
`detect_from_labels` is the offline stand-in that reads CAD text labels.
**The VLM never returns a measurement** — only classification, a name, a printed
area, and a callout mark.

### `ingest/ocr.py`
One process-wide RapidOCR engine, reused by both scanned floor-plan labels and the
codebook LLM parser's scan-verification. Degrades to empty results when RapidOCR
(shipped with the `docling` extra) is unavailable.

---

## 8. Backend: semantic ingestion

Turns a codebook PDF into a `RegulationClause` hierarchy.

### `ingest/semantic.py`
`resolve_parser_mode(configured, vision_model)` picks the parser: **`auto` is
LLM-first** when a vision key is present (one-time cost, the graph caches clauses),
else Docling if installed, else the simple regex parser. `parse_codebook_isolated`
runs Docling in short-lived, serialized worker subprocesses (its parse backend
commits ~2 GB/page, so conversion is chunked and isolated).

### `ingest/llm_parser.py`
The LLM-first path. A **per-page router**: clean single-column, table-free pages
are parsed straight from the text layer (no VLM call); the rest are VLM-transcribed
to markdown and **verified against the PDF's own text layer** (or OCR for scans) —
a coarse token-recall floor **and** an exact dimension cross-check (a flipped
`32"→36"` is bounced), retried, then on failure the page falls back to its text
layer and is flagged. One bad page never aborts the parse. Returns
`(clauses, unverified_count, failed_pages)`.

### `ingest/clause_tree.py`
`build_clause_tree_from_structure` derives hierarchy purely from clause-id dotted
prefixes (`404.2.3` → parent `404.2`), with a heading-depth fallback for
unnumbered/outline headings. `StructuredItem` is the neutral shape all parser
paths feed into. TOC/index/running-head furniture is dropped.

### `ingest/embeddings.py`
Local `fastembed` ONNX **bge-small** (384-dim). No API key, no cost, deterministic
— the same embedder used at ingest (clause vectors) and query (Code Hunter).

---

## 9. Backend: verification

### `verify/pipeline.py`
- `run_full` — the API entry point: ingest anything pending, then verify, emitting
  a `done`/`error` event.
- `ingest_pending_documents` — lazy ingestion; offloads sync CPU-bound work
  (PDF parse, embed) via `asyncio.to_thread` so the event loop (and the run's own
  SSE stream) stays live.
- `run_verification` — the loop: for each asset, `hunt` → `_constraints_for` →
  `check` → `write_verdict`. **Per-asset `try/except`**: an error records a
  `NEEDS_REVIEW` fallback verdict and continues. A constraint governing this
  asset's type but not verdictable writes an explicit `NEEDS_REVIEW`; one governing
  a *different* type writes no edge (a door-width rule never tags a window). The
  fallback `NEEDS_REVIEW` fires only when the retrieved clauses yield no
  machine-checkable constraint at all — never a silent gap.
- `_constraints_for` — the cache gate: `constraints_extracted?` → return cached,
  else extract (real or offline stand-in) → `save_constraints` + `mark_constraints_extracted`.

### `verify/code_hunter.py`
`hunt(asset, project_id, repo, embedder, k)` — deterministic retrieval: embed the
asset's type/measurements as a query, `vector_search` over clause embeddings, and
expand each hit with its **full ancestral chain** (`ancestors`) so the extractor
sees a clause in the context of its Chapter/Section. No LLM here.

### `verify/rule_extractor.py`
`extract_constraints(clause, ancestors, text_model)` — **the one place an LLM
interprets legal text**. Pydantic AI turns clause prose into typed `Constraint`s,
guarded by **output validators** that bounce implausible values back via
`ModelRetry` (e.g. a slope outside a plausible range) before anything enters the
graph.

### `verify/offline_extractor.py`
`offline_extract(clause)` — deterministic regex extraction used when
`PLANLINT_OFFLINE_SAMPLE=1`, so the sample verifies fully offline.

### `verify/checker.py` — the verdict authority
**Pure Python, the only component that decides compliance.** No LLM performs
arithmetic or renders judgment here.
- `to_inches` / `to_square_metres` — normalize a constraint's value to canonical
  units before comparison.
- `check(asset, constraint)` — dispatches by parameter (`_check_area`,
  `_check_slope`, and the generic length path) and by operator (min/max/range).
  Returns `COMPLIES_WITH` / `VIOLATES` with `measured` + `required`, or
  `NEEDS_REVIEW` (`_review`) when the asset lacks the measurement, the scale was
  never found, or the clause is qualitative. Returns `None` when the constraint
  doesn't apply to this asset at all (no edge written).

---

## 10. The deterministic seam in detail

This is the part to protect. Two things make a PlanLint verdict trustworthy:
**(a)** every measurement is grounded before it can be checked, and **(b)** the
checker — not a model — compares.

### Opening clear-width precedence (highest → lowest)
1. **Schedule join** (`source="schedule"`) — the opening's callout mark → schedule
   size. A printed spec keyed by the plan tag.
2. **Dimension grid** — a printed dimension validated `value ≈ span × scale`.
3. **Nearby dimension label** — a clean CAD dimension within range (notes rejected).
4. **Wall-gap width** — the snapped flanked gap × scale.
5. **Longest interior segment × scale** — last-resort geometry (openings only).
6. **Nothing matched** → no measurement → the checker returns `NEEDS_REVIEW`.

### Room area
Grid width×depth, **or** a genuinely printed area verified against the text layer
(`_printed_area_present`), **or** raster-fill-confirmed. The VLM's bare
`floor_area_m2` is **dropped** when the sheet prints no matching area (residential
plans print none) — a hallucinated area never enters the graph. A room **never**
takes a clear width from the longest-segment heuristic (that's opening-only).

### Provenance → confidence
| `source` | confidence | meaning |
|---|---|---|
| `schedule` | 0.95 | joined to a printed schedule size |
| `vector-snapped` | 0.95 | box snapped to real CAD geometry |
| `raster-snapped` | 0.80 | box snapped to pixel wall-mask |
| `vlm-only` | 0.60 | model box, unconfirmed by geometry |

### Grounding validations (the "earn the right to be checked" gates)
- Dimension text trusted only if it matches its line length × scale.
- Transcribed codebook dimensions cross-checked against the page text layer.
- Structural-note numbers (`… @ 16" OC`) never read as widths.
- Opening claims *refuted* when hatching/fill occupies the gap.

---

## 11. Frontend

Next.js App Router; Tailwind theme tokens (dark-only); optimistic mutations
reconciled on failure; effect cleanup mandatory.

**Routes**
- `app/page.tsx` — home: create/load projects, manage the list, seed the sample.
- `app/project/[id]/page.tsx` — the workspace: uploads, run controls, and the
  two-pane review layout.

**Components**
- `PlanViewer.tsx` — **left pane**: renders a sheet's PDF page (pdf.js) and
  overlays asset boxes colored by worst verdict; selecting one spotlights it (the
  rest dim, the selection glows) and centers it in view.
- `CodePane.tsx` — **right pane**: the codebook clause tree annotated with
  verdicts. Selecting an asset spotlights its governing clauses (the rest fade),
  and each clause lists every asset it governs as clickable chips — the reverse of
  the inspector's cross-reference.
- `AssetInspector.tsx` — the selected asset's detail card (what it is, how it was
  measured, its governing clauses), a distinct accent card pinned atop the right pane.
- `AssetIndex.tsx` — browse every asset on the sheet, grouped worst-verdict-first.
- `CanvasLegend.tsx` — the overlay key that is also a verdict filter.
- `CodebookModal.tsx` — renders one codebook PDF page with the clause boxed
  ("view in codebook").
- `RunProgress.tsx` — live run feedback (latest message + progress bar + full log).
- `Toasts.tsx` — transient success notices (errors are persistent, elsewhere).
- `ConfirmDialog.tsx` — minimal accessible confirm modal.

**lib**
- `api.ts` — the single HTTP client; every backend call lives here.
- `types.ts` — mirrors the `GET /projects/{id}/results` payload (keep in sync with
  the backend).
- `assets.ts` — single source of truth for how an asset presents (label, type
  code, the one measurement worth showing, `sourceQuality`).
- `verdicts.ts` — single source of truth for verdict glyph/word/color.
- `useVerificationRun.ts` — owns a run's lifecycle: start → SSE events →
  done/error; drives `RunProgress` and triggers a results refetch.

---

## 12. Testing architecture

**No test hits an LLM or a live database by default.**
- Agent tests use Pydantic AI `TestModel`/`FunctionModel` — zero API calls.
- Graph tests use a `FakeRepository` (injected via `create_app`); a `FakeEmbedder`
  stands in for fastembed.
- Only `-m integration` needs Neo4j (`docker compose up -d neo4j`);
  `-m docling` exercises the real Docling parser (slow).
- Geometry/schedule/dimension logic is tested against **synthetic PDFs and
  primitives** built inline with PyMuPDF — deterministic, offline.
- Snapshots (`inline_snapshot`, pinned non-interactive) assert the **structured
  verdict set**, never LLM prose — a diff means behavior changed, not sampling
  noise. Update intentionally with `--inline-snapshot=fix`.

**`PLANLINT_OFFLINE_SAMPLE=1`** runs the whole bundled sample offline: a deterministic
label reader parses the CAD labels, a regex extractor reads clauses. This is the
demo/E2E mode and the CI determinism guarantee.

---

## 13. Configuration & runtime

`config.py` centralizes every knob (`settings`): model strings, Neo4j connection,
data/samples dirs, parser mode, `planlint_offline_sample`, Docling chunk size.

**Runtime discipline**
- **Event loop stays responsive.** All sync CPU-bound work (PDF parse, embed,
  per-page geometry, raster analysis, OCR) is offloaded via `asyncio.to_thread`;
  the already-async Pydantic AI agent calls stay as-is. Keep new blocking library
  calls off the loop.
- **Per-page / per-asset isolation** everywhere: ingestion and verification both
  wrap each unit in try/except so one bad input degrades to a warning + a recorded
  empty/`NEEDS_REVIEW`, never an aborted run.
- **Docker**: `docker compose up --build` runs `api` + `web` + `neo4j` (compose
  project `planlint`). Neo4j Browser at `:7474`, API at `:8000`, UI at `:3000`.
- **Data**: uploaded PDFs live under `data/{project_id}/`; the graph holds
  everything else. Deleting a project cascades the graph and removes the files.

---

## 14. Where to extend

- **A new asset type** → add to `AssetType`, teach the VLM prompt (`vlm.py`) to
  classify it, add any measurement to `Parameter`, and (if geometric) a grounding
  path in `spatial.py`. The checker generalizes over `Parameter`/`Operator`, so
  min/max/range checks need no checker change.
- **A new measurable parameter** → add to `Parameter`, produce it in spatial
  ingestion with a real provenance, and (if it needs special comparison, like area
  or slope) a `_check_*` branch in `checker.py`.
- **A new grounding source for a measurement** → add it to the precedence chain in
  `spatial.py` (§10) with an explicit `source`/confidence; never let a model number
  bypass grounding.
- **A new codebook format** → most likely a new parser path feeding the one
  `build_clause_tree_from_structure`; wire it through `resolve_parser_mode`.
- **A new API capability** → a thin handler in `routes.py` + a repository method in
  `repository.py` (all Cypher stays there). Keep boundaries as `models.py` types.

**The one rule when extending:** structure extraction and verdict decision stay
separate. A model may propose *what* something is; only `checker.py` may decide
*whether it complies*, and only over a value that geometry or printed text has
grounded.
