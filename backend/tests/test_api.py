"""API tests over the ASGI app with the in-memory repository injected."""

import asyncio
import json

import httpx
import pytest

from planlint.api.main import create_app
from planlint.models import CheckResult, Constraint, PhysicalAsset, RegulationClause  # noqa: F401
from tests.conftest import FakeEmbedder, FakeRepository


class ApiFakeRepository(FakeRepository):
    """Extends the pipeline fake with the project/document/results methods
    the routes need."""

    def __init__(self):
        super().__init__()
        self.projects: dict[str, dict] = {}

    async def create_project(self, project_id: str, name: str) -> None:
        self.projects[project_id] = {"id": project_id, "name": name}

    async def list_projects(self):
        return [{**p, "document_count": 0} for p in self.projects.values()]

    async def create_document(self, document_id, project_id, kind, filename, path) -> None:
        self.documents[document_id] = {
            "id": document_id, "project_id": project_id, "kind": kind,
            "filename": filename, "path": path, "ingested": True,  # skip real ingestion
        }

    async def get_document(self, document_id):
        return self.documents.get(document_id)

    async def results_payload(self, project_id, run_id=None):
        if project_id not in self.projects:
            return {}
        return {
            "project": self.projects[project_id],
            "documents": [d for d in self.documents.values() if d["project_id"] == project_id],
            "sheets": [],
            "clauses": [],
        }

    async def set_document_manual_scale(self, document_id, scale_text) -> None:
        self.documents[document_id]["manual_scale_text"] = scale_text
        self.documents[document_id]["ingested"] = False


@pytest.fixture
async def client(tmp_path, monkeypatch):
    from planlint.config import settings

    monkeypatch.setattr(settings, "planlint_data_dir", tmp_path)
    monkeypatch.setattr(settings, "planlint_fake_llm", True)
    app = create_app(repo=ApiFakeRepository(), embedder=FakeEmbedder())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        async with app.router.lifespan_context(app):
            yield http, app


async def test_create_and_list_projects(client):
    http, _ = client
    created = await http.post("/projects", json={"name": "Tower A"})
    assert created.status_code == 201
    listed = await http.get("/projects")
    assert listed.json()[0]["name"] == "Tower A"


async def test_upload_document_stores_file(client, tmp_path):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    response = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "codebook"},
        files={"file": ("ada.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 201
    document = await app.state.repo.get_document(response.json()["id"])
    assert document is not None
    with open(document["path"], "rb") as fh:
        assert fh.read().startswith(b"%PDF")


async def test_upload_rejects_bad_kind(client):
    http, _ = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    response = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "blueprint"},
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 422


async def test_verify_streams_events_to_done(client):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    started = await http.post(f"/projects/{project['id']}/verify")
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    # Let the background job finish (no documents -> immediate done).
    run = app.state.runs.get(run_id)
    await asyncio.wait_for(run.task, timeout=10)

    events = []
    async with http.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))
    assert events[-1]["stage"] == "done"


async def test_results_unknown_project_404(client):
    http, _ = client
    assert (await http.get("/projects/nope/results")).status_code == 404


async def test_scale_patch_validates(client):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    upload = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "floorplan"},
        files={"file": ("plan.pdf", b"%PDF-1.4", "application/pdf")},
    )
    document_id = upload.json()["id"]

    bad = await http.patch(f"/documents/{document_id}/scale", json={"scale_text": "banana"})
    assert bad.status_code == 422

    good = await http.patch(
        f"/documents/{document_id}/scale", json={"scale_text": '1/4" = 1\'-0"'}
    )
    assert good.status_code == 200
    document = await app.state.repo.get_document(document_id)
    assert document["manual_scale_text"] == '1/4" = 1\'-0"'
    assert document["ingested"] is False
