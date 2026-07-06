# PlanLint backend

FastAPI service: ingestion (spatial + semantic), verification engine, Neo4j graph.
See the repository root README for the full quickstart.

```bash
uv sync                 # install
uv run pytest           # unit tests (no LLM, no Neo4j needed)
uv run pytest -m integration   # needs a live Neo4j (docker compose up neo4j)
uv run uvicorn planlint.api.main:app --reload
```
