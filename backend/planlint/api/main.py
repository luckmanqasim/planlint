"""App factory. Tests inject fakes via create_app(); production wires the
real Neo4j driver and fastembed embedder in the lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from planlint.api.routes import router
from planlint.api.runs import RunManager
from planlint.config import settings


def create_app(repo=None, embedder=None) -> FastAPI:
    """Build the FastAPI app. Pass `repo`/`embedder` to inject fakes in tests;
    leave them None and the lifespan wires the real Neo4j driver and embedder."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        driver = None
        if repo is None:
            from neo4j import AsyncGraphDatabase

            from planlint.graph.repository import GraphRepository

            driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
            )
            app.state.repo = GraphRepository(driver)
            await app.state.repo.init_schema()
        else:
            app.state.repo = repo

        if embedder is None:
            from planlint.ingest.embeddings import Embedder

            app.state.embedder = Embedder()
        else:
            app.state.embedder = embedder

        app.state.runs = RunManager()
        settings.planlint_data_dir.mkdir(parents=True, exist_ok=True)
        yield
        if driver is not None:
            await driver.close()

    app = FastAPI(title="PlanLint", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
