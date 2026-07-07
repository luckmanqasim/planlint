"""HTTP surface. Thin: every route delegates to the repository or pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from planlint.config import settings
from planlint.ingest.vector_geometry import parse_scale
from planlint.models import RunEvent
from planlint.verify.pipeline import run_full

router = APIRouter()

logger = logging.getLogger(__name__)

# project_id becomes a directory name under the data dir; reject anything
# that couldn't have been produced by our own id generator.
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _validate_project_id(project_id: str) -> None:
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise HTTPException(404, "unknown project")


def _repo(request: Request):
    return request.app.state.repo


class CreateProject(BaseModel):
    name: str


class ScaleUpdate(BaseModel):
    scale_text: str  # e.g. '1/4" = 1\'-0"' or '1:50'


@router.get("/projects")
async def list_projects(request: Request):
    return await _repo(request).list_projects()


@router.post("/projects", status_code=201)
async def create_project(body: CreateProject, request: Request):
    project_id = f"proj-{uuid.uuid4().hex[:10]}"
    await _repo(request).create_project(project_id, body.name)
    return {"id": project_id, "name": body.name}


@router.post("/projects/sample", status_code=201)
async def create_sample_project(request: Request):
    """Bundled sample: vector floor plan + ADA Standards excerpt."""
    samples = Path(settings.planlint_samples_dir)
    plan = samples / "sample_floorplan.pdf"
    code = samples / "ada_excerpt.pdf"
    if not plan.exists() or not code.exists():
        raise HTTPException(500, "Sample PDFs missing — run the generators in samples/")
    repo = _repo(request)
    project_id = f"proj-{uuid.uuid4().hex[:10]}"
    await repo.create_project(project_id, "Sample: ADA door clearance audit")
    for source, kind in ((plan, "floorplan"), (code, "codebook")):
        document_id = f"doc-{uuid.uuid4().hex[:10]}"
        destination = Path(settings.planlint_data_dir) / project_id / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, destination)
        await repo.create_document(document_id, project_id, kind, source.name, str(destination))
    return {"id": project_id}


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request):
    _validate_project_id(project_id)
    paths = await _repo(request).delete_project(project_id)
    if paths is None:
        raise HTTPException(404, "unknown project")
    request.app.state.runs.drop_project(project_id)
    for path in paths:
        if path:
            Path(path).unlink(missing_ok=True)
    shutil.rmtree(Path(settings.planlint_data_dir) / project_id, ignore_errors=True)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, request: Request):
    info = await _repo(request).delete_document(document_id)
    if info is None:
        raise HTTPException(404, "document not found")
    if info.get("path"):
        Path(info["path"]).unlink(missing_ok=True)


@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_document(project_id: str, kind: str, file: UploadFile, request: Request):
    _validate_project_id(project_id)
    if kind not in ("floorplan", "codebook"):
        raise HTTPException(422, "kind must be floorplan or codebook")
    filename = Path(file.filename or "upload.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(422, "only PDF files are supported")
    if await _repo(request).get_project(project_id) is None:
        raise HTTPException(404, "unknown project")
    document_id = f"doc-{uuid.uuid4().hex[:10]}"
    data_dir = Path(settings.planlint_data_dir).resolve()
    destination = (data_dir / project_id / f"{document_id}-{filename}").resolve()
    if not destination.is_relative_to(data_dir):
        raise HTTPException(400, "invalid path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    try:
        await _repo(request).create_document(
            document_id, project_id, kind, filename, str(destination)
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"id": document_id, "kind": kind, "filename": filename}


@router.post("/projects/{project_id}/verify", status_code=202)
async def start_verification(project_id: str, request: Request):
    _validate_project_id(project_id)
    if await _repo(request).get_project(project_id) is None:
        raise HTTPException(404, "unknown project")
    app = request.app
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    manager = app.state.runs
    manager.create(run_id, project_id)

    async def emit(event: RunEvent) -> None:
        await manager.emit(run_id, event)

    async def job() -> None:
        try:
            await run_full(
                project_id,
                app.state.repo,
                app.state.embedder,
                settings.planlint_vision_model,
                settings.planlint_text_model,
                run_id=run_id,
                emit=emit,
            )
        except Exception:  # run_full already emitted the error event
            logger.exception("verification run %s failed", run_id)

    run = manager.get(run_id)
    run.task = asyncio.create_task(job())
    return {"run_id": run_id}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    manager = request.app.state.runs
    if manager.get(run_id) is None:
        raise HTTPException(404, "unknown run")

    async def stream():
        async for event in manager.subscribe(run_id):
            yield {"event": "progress", "data": event.model_dump_json()}

    return EventSourceResponse(stream())


@router.get("/projects/{project_id}/results")
async def project_results(project_id: str, request: Request, run_id: str | None = None):
    payload = await _repo(request).results_payload(project_id, run_id)
    if not payload:
        raise HTTPException(404, "unknown project")
    return payload


@router.get("/documents/{document_id}/pdf")
async def document_pdf(document_id: str, request: Request):
    document = await _repo(request).get_document(document_id)
    if document is None or not Path(document["path"]).exists():
        raise HTTPException(404, "document not found")
    return FileResponse(document["path"], media_type="application/pdf")


@router.patch("/documents/{document_id}/scale")
async def set_document_scale(document_id: str, body: ScaleUpdate, request: Request):
    if parse_scale(body.scale_text) is None:
        raise HTTPException(422, "unparseable scale — use forms like '1/4\" = 1'-0\"' or '1:50'")
    repo = _repo(request)
    if await repo.get_document(document_id) is None:
        raise HTTPException(404, "document not found")
    await repo.set_document_manual_scale(document_id, body.scale_text)
    return {"status": "scale set — re-run verification to re-ingest"}
