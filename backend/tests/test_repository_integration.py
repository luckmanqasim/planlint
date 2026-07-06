"""Integration tests against a live Neo4j (docker compose up -d neo4j).

Run with: uv run pytest -m integration
Skipped automatically when Neo4j is unreachable."""

import uuid

import pytest
from neo4j import AsyncGraphDatabase

from planlint.config import settings
from planlint.graph.repository import GraphRepository
from planlint.models import (
    AssetType,
    CheckResult,
    Parameter,
    PhysicalAsset,
    RegulationClause,
    VerdictType,
)
from tests.conftest import FakeEmbedder

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo():
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
    except Exception:
        await driver.close()
        pytest.skip("Neo4j not reachable — start it with: docker compose up -d neo4j")
    repository = GraphRepository(driver)
    await repository.init_schema()
    yield repository
    await driver.close()


@pytest.fixture
def project_id() -> str:
    return f"proj-test-{uuid.uuid4().hex[:8]}"


async def seed_project(repo: GraphRepository, project_id: str) -> dict:
    await repo.create_project(project_id, "integration test")
    doc_plan = f"doc-{uuid.uuid4().hex[:8]}"
    doc_code = f"doc-{uuid.uuid4().hex[:8]}"
    await repo.create_document(doc_plan, project_id, "floorplan", "plan.pdf", "/tmp/plan.pdf")
    await repo.create_document(doc_code, project_id, "codebook", "ada.pdf", "/tmp/ada.pdf")

    parent = RegulationClause(clause_id="404", title="Doors", text="404 Doors")
    child = RegulationClause(
        clause_id="404.2.3",
        title="Clear Width",
        text="404.2.3 Clear Width. Door openings shall provide a clear width of "
        "32 inches (815 mm) minimum.",
        parent_clause_id="404",
    )
    embedder = FakeEmbedder()
    await repo.upsert_clauses(
        doc_code, [parent, child], embedder.embed([parent.text, child.text])
    )

    sheet_id = f"sheet-{uuid.uuid4().hex[:8]}"
    await repo.create_sheet(sheet_id, doc_plan, 0, 612, 792, '1/4" = 1\'-0"', 48 / 72)
    asset = PhysicalAsset(
        type=AssetType.DOOR,
        label="D2",
        bbox=(10, 10, 50, 20),
        source="vector-snapped",
        measurements={Parameter.CLEAR_WIDTH: 30.0},
    )
    await repo.upsert_assets(sheet_id, [asset])
    return {"asset": asset, "child": child, "embedder": embedder}


async def test_schema_init_is_idempotent(repo):
    await repo.init_schema()
    await repo.init_schema()


async def test_clause_roundtrip_and_vector_search(repo, project_id):
    seeded = await seed_project(repo, project_id)
    embedder = seeded["embedder"]
    hits = await repo.vector_search(
        project_id, embedder.embed_one("door clear width requirement")
    )
    assert hits, "vector search returned nothing"
    assert hits[0]["clause"]["clause_id"] == "404.2.3"

    ancestors = await repo.ancestors(seeded["child"].id)
    assert [a["clause_id"] for a in ancestors] == ["404"]


async def test_verdict_merge_is_idempotent(repo, project_id):
    seeded = await seed_project(repo, project_id)
    result = CheckResult(
        verdict=VerdictType.VIOLATES,
        measured=30.0,
        required=">= 32 in",
        reason="clear_width is 30 inches, code requires 32 inches minimum",
    )
    for _ in range(3):  # same run -> exactly one edge
        await repo.write_verdict(seeded["asset"].id, seeded["child"].id, "run-A", result)
    await repo.write_verdict(seeded["asset"].id, seeded["child"].id, "run-B", result)

    rows = await repo._run(
        "MATCH (:PhysicalAsset {id: $aid})-[v:VIOLATES]->(:Regulation {id: $rid}) "
        "RETURN v.run_id AS run_id ORDER BY run_id",
        aid=seeded["asset"].id,
        rid=seeded["child"].id,
    )
    assert [row["run_id"] for row in rows] == ["run-A", "run-B"]


async def test_results_payload_shape(repo, project_id):
    seeded = await seed_project(repo, project_id)
    await repo.write_verdict(
        seeded["asset"].id,
        seeded["child"].id,
        "run-A",
        CheckResult(verdict=VerdictType.VIOLATES, measured=30.0, required=">= 32 in", reason="r"),
    )
    payload = await repo.results_payload(project_id)
    assert payload["project"]["id"] == project_id
    assert len(payload["documents"]) == 2
    assert len(payload["sheets"]) == 1
    sheet = payload["sheets"][0]
    assert sheet["scale_in_per_point"] == pytest.approx(48 / 72)
    asset = sheet["assets"][0]
    assert asset["measurements"] == {"clear_width": 30.0}
    assert asset["verdicts"][0]["verdict"] == "VIOLATES"
    assert asset["verdicts"][0]["clause_id"] == "404.2.3"
    assert {c["clause_id"] for c in payload["clauses"]} == {"404", "404.2.3"}
