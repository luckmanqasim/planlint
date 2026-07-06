"""GraphRepository: every Cypher statement in the system lives here.

All writes are parameterized; relationship types are interpolated only from
the VerdictType enum (closed set), never from user input.
"""

from __future__ import annotations

import json
from typing import Any

from neo4j import AsyncDriver

from planlint.config import settings
from planlint.models import (
    CheckResult,
    Constraint,
    PhysicalAsset,
    RegulationClause,
    VerdictType,
)


class GraphRepository:
    def __init__(self, driver: AsyncDriver, database: str = "neo4j"):
        self._driver = driver
        self._db = database

    async def _run(self, query: str, **params: Any) -> list[dict]:
        async with self._driver.session(database=self._db) as session:
            result = await session.run(query, **params)
            return [record.data() async for record in result]

    # ------------------------------------------------------------- schema

    async def init_schema(self) -> None:
        for label in ("Project", "Document", "Sheet", "PhysicalAsset", "Regulation", "Constraint"):
            await self._run(
                f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )
        await self._run(
            "CREATE VECTOR INDEX regulation_embedding IF NOT EXISTS "
            "FOR (r:Regulation) ON (r.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: $dims, "
            "`vector.similarity_function`: 'cosine'}}",
            dims=settings.embed_dimensions,
        )

    # ------------------------------------------------------------ projects

    async def create_project(self, project_id: str, name: str) -> None:
        await self._run(
            "CREATE (p:Project {id: $id, name: $name, created_at: datetime()})",
            id=project_id,
            name=name,
        )

    async def list_projects(self) -> list[dict]:
        return await self._run(
            "MATCH (p:Project) "
            "OPTIONAL MATCH (p)-[:HAS_DOCUMENT]->(d:Document) "
            "RETURN p.id AS id, p.name AS name, toString(p.created_at) AS created_at, "
            "count(d) AS document_count ORDER BY created_at DESC"
        )

    # ----------------------------------------------------------- documents

    async def create_document(
        self, document_id: str, project_id: str, kind: str, filename: str, path: str
    ) -> None:
        await self._run(
            "MATCH (p:Project {id: $project_id}) "
            "CREATE (p)-[:HAS_DOCUMENT]->(:Document {id: $id, kind: $kind, filename: $filename, "
            "path: $path, pdf_type: null, ingested: false, project_id: $project_id})",
            project_id=project_id,
            id=document_id,
            kind=kind,
            filename=filename,
            path=path,
        )

    async def get_document(self, document_id: str) -> dict | None:
        rows = await self._run(
            "MATCH (d:Document {id: $id}) RETURN d { .* } AS doc", id=document_id
        )
        return rows[0]["doc"] if rows else None

    async def get_documents(self, project_id: str) -> list[dict]:
        return [
            row["doc"]
            for row in await self._run(
                "MATCH (:Project {id: $pid})-[:HAS_DOCUMENT]->(d:Document) "
                "RETURN d { .* } AS doc ORDER BY d.filename",
                pid=project_id,
            )
        ]

    async def set_document_manual_scale(self, document_id: str, scale_text: str) -> None:
        """Store a user-entered scale and force re-ingestion: existing sheets
        and their assets (with all verdict edges) are removed."""
        await self._run(
            "MATCH (d:Document {id: $id}) "
            "SET d.manual_scale_text = $scale_text, d.ingested = false "
            "WITH d OPTIONAL MATCH (d)-[:HAS_SHEET]->(s:Sheet) "
            "OPTIONAL MATCH (s)-[:CONTAINS]->(a:PhysicalAsset) "
            "DETACH DELETE s, a",
            id=document_id,
            scale_text=scale_text,
        )

    async def mark_ingested(self, document_id: str, pdf_type: str | None = None) -> None:
        await self._run(
            "MATCH (d:Document {id: $id}) SET d.ingested = true, "
            "d.pdf_type = coalesce($pdf_type, d.pdf_type)",
            id=document_id,
            pdf_type=pdf_type,
        )

    # -------------------------------------------------------------- sheets

    async def create_sheet(
        self,
        sheet_id: str,
        document_id: str,
        page_number: int,
        width: float,
        height: float,
        scale_text: str | None,
        scale_in_per_point: float | None,
    ) -> None:
        await self._run(
            "MATCH (d:Document {id: $document_id}) "
            "MERGE (d)-[:HAS_SHEET]->(s:Sheet {id: $id}) "
            "SET s.page_number = $page_number, s.width = $width, s.height = $height, "
            "s.scale_text = $scale_text, s.scale_in_per_point = $scale_in_per_point, "
            "s.project_id = d.project_id",
            document_id=document_id,
            id=sheet_id,
            page_number=page_number,
            width=width,
            height=height,
            scale_text=scale_text,
            scale_in_per_point=scale_in_per_point,
        )

    async def set_sheet_scale(
        self, sheet_id: str, scale_text: str, scale_in_per_point: float
    ) -> None:
        await self._run(
            "MATCH (s:Sheet {id: $id}) "
            "SET s.scale_text = $scale_text, s.scale_in_per_point = $scale_in_per_point",
            id=sheet_id,
            scale_text=scale_text,
            scale_in_per_point=scale_in_per_point,
        )

    async def get_sheet(self, sheet_id: str) -> dict | None:
        rows = await self._run("MATCH (s:Sheet {id: $id}) RETURN s { .* } AS s", id=sheet_id)
        return rows[0]["s"] if rows else None

    # -------------------------------------------------------------- assets

    async def upsert_assets(self, sheet_id: str, assets: list[PhysicalAsset]) -> None:
        await self._run(
            "MATCH (s:Sheet {id: $sheet_id}) "
            "UNWIND $assets AS a "
            "MERGE (s)-[:CONTAINS]->(n:PhysicalAsset {id: a.id}) "
            "SET n.type = a.type, n.label = a.label, n.bbox = a.bbox, "
            "n.confidence = a.confidence, n.source = a.source, "
            "n.measurements = a.measurements, n.project_id = s.project_id",
            sheet_id=sheet_id,
            assets=[
                {
                    "id": asset.id,
                    "type": asset.type.value,
                    "label": asset.label,
                    "bbox": list(asset.bbox),
                    "confidence": asset.confidence,
                    "source": asset.source,
                    "measurements": json.dumps(
                        {k.value: v for k, v in asset.measurements.items()}
                    ),
                }
                for asset in assets
            ],
        )

    async def get_assets(self, project_id: str) -> list[dict]:
        rows = await self._run(
            "MATCH (s:Sheet {project_id: $pid})-[:CONTAINS]->(a:PhysicalAsset) "
            "RETURN a { .* } AS asset, s.id AS sheet_id",
            pid=project_id,
        )
        out = []
        for row in rows:
            asset = row["asset"]
            asset["measurements"] = json.loads(asset.get("measurements") or "{}")
            asset["sheet_id"] = row["sheet_id"]
            out.append(asset)
        return out

    # ------------------------------------------------------------- clauses

    async def upsert_clauses(
        self,
        document_id: str,
        clauses: list[RegulationClause],
        embeddings: list[list[float]],
    ) -> None:
        assert len(clauses) == len(embeddings)
        await self._run(
            "MATCH (d:Document {id: $document_id}) "
            "UNWIND $clauses AS c "
            "MERGE (d)-[:HAS_CLAUSE]->(r:Regulation {id: c.id}) "
            "SET r.clause_id = c.clause_id, r.title = c.title, "
            "r.hierarchy_path = c.hierarchy_path, r.text = c.text, r.page = c.page, "
            "r.bbox = c.bbox, r.document_id = $document_id, r.project_id = d.project_id "
            "WITH r, c CALL db.create.setNodeVectorProperty(r, 'embedding', c.embedding) "
            "RETURN count(r)",
            document_id=document_id,
            clauses=[
                {
                    "id": clause.id,
                    "clause_id": clause.clause_id,
                    "title": clause.title,
                    "hierarchy_path": clause.hierarchy_path,
                    "text": clause.text,
                    "page": clause.page,
                    "bbox": list(clause.bbox) if clause.bbox else None,
                    "embedding": embedding,
                }
                for clause, embedding in zip(clauses, embeddings)
            ],
        )
        # Second pass: PARENT_OF edges via (document_id, clause_id) lookups.
        await self._run(
            "UNWIND $links AS link "
            "MATCH (parent:Regulation {document_id: $document_id, clause_id: link.parent}) "
            "MATCH (child:Regulation {document_id: $document_id, clause_id: link.child}) "
            "MERGE (parent)-[:PARENT_OF]->(child)",
            document_id=document_id,
            links=[
                {"parent": clause.parent_clause_id, "child": clause.clause_id}
                for clause in clauses
                if clause.parent_clause_id
            ],
        )

    async def get_clauses(self, project_id: str) -> list[dict]:
        return [
            row["clause"]
            for row in await self._run(
                "MATCH (r:Regulation {project_id: $pid}) "
                "RETURN r {.id, .clause_id, .title, .hierarchy_path, .text, .page, .bbox, "
                ".document_id} AS clause ORDER BY r.clause_id",
                pid=project_id,
            )
        ]

    async def vector_search(
        self, project_id: str, embedding: list[float], k: int | None = None
    ) -> list[dict]:
        k = k or settings.retrieval_top_k
        # Over-fetch, then scope to this project's codebooks.
        rows = await self._run(
            "CALL db.index.vector.queryNodes('regulation_embedding', $overfetch, $embedding) "
            "YIELD node, score "
            "WHERE node.project_id = $pid "
            "RETURN node {.id, .clause_id, .title, .hierarchy_path, .text, .page, .bbox, "
            ".document_id} AS clause, score "
            "ORDER BY score DESC LIMIT $k",
            overfetch=k * 5,
            embedding=embedding,
            pid=project_id,
            k=k,
        )
        return rows  # [{clause: {...}, score: float}]

    async def ancestors(self, regulation_id: str) -> list[dict]:
        """Full ancestral chain, root first (Chapter → Section → …)."""
        return [
            row["clause"]
            for row in await self._run(
                "MATCH path = (a:Regulation)-[:PARENT_OF*1..6]->(m:Regulation {id: $id}) "
                "RETURN a {.id, .clause_id, .title, .text} AS clause, length(path) AS depth "
                "ORDER BY depth DESC",
                id=regulation_id,
            )
        ]

    # ---------------------------------------------------------- constraints

    async def get_constraints(self, regulation_id: str) -> list[Constraint]:
        rows = await self._run(
            "MATCH (:Regulation {id: $id})-[:DEFINES]->(c:Constraint) RETURN c { .* } AS c",
            id=regulation_id,
        )
        return [Constraint.model_validate(row["c"]) for row in rows]

    async def save_constraints(self, constraints: list[Constraint]) -> None:
        await self._run(
            "UNWIND $constraints AS c "
            "MATCH (r:Regulation {id: c.regulation_id}) "
            "MERGE (r)-[:DEFINES]->(n:Constraint {id: c.id}) "
            "SET n += c",
            constraints=[
                {
                    **constraint.model_dump(mode="json"),
                }
                for constraint in constraints
            ],
        )

    async def mark_constraints_extracted(self, regulation_id: str) -> None:
        await self._run(
            "MATCH (r:Regulation {id: $id}) SET r.constraints_extracted = true", id=regulation_id
        )

    async def constraints_extracted(self, regulation_id: str) -> bool:
        rows = await self._run(
            "MATCH (r:Regulation {id: $id}) RETURN coalesce(r.constraints_extracted, false) AS x",
            id=regulation_id,
        )
        return bool(rows and rows[0]["x"])

    # ------------------------------------------------------------- verdicts

    async def write_verdict(
        self, asset_id: str, regulation_id: str, run_id: str, result: CheckResult
    ) -> None:
        rel = VerdictType(result.verdict).value  # closed enum -> safe to interpolate
        await self._run(
            f"MATCH (a:PhysicalAsset {{id: $asset_id}}), (r:Regulation {{id: $regulation_id}}) "
            f"MERGE (a)-[v:{rel} {{run_id: $run_id}}]->(r) "
            f"SET v.checked_at = datetime(), v.measured = $measured, "
            f"v.required = $required, v.reason = $reason",
            asset_id=asset_id,
            regulation_id=regulation_id,
            run_id=run_id,
            measured=result.measured,
            required=result.required,
            reason=result.reason,
        )

    # -------------------------------------------------------------- results

    async def latest_run_id(self, project_id: str) -> str | None:
        rows = await self._run(
            "MATCH (a:PhysicalAsset {project_id: $pid})-[v]->(:Regulation) "
            "WHERE v.run_id IS NOT NULL "
            "RETURN v.run_id AS run_id, max(v.checked_at) AS latest "
            "ORDER BY latest DESC LIMIT 1",
            pid=project_id,
        )
        return rows[0]["run_id"] if rows else None

    async def results_payload(self, project_id: str, run_id: str | None = None) -> dict:
        """Everything the dual-pane UI needs, in one shape. Defaults to the
        most recent run's verdicts (prior runs stay in the graph as audit
        history, reachable by passing an explicit run_id)."""
        projects = await self._run(
            "MATCH (p:Project {id: $pid}) RETURN p {.id, .name} AS p", pid=project_id
        )
        if not projects:
            return {}
        if run_id is None:
            run_id = await self.latest_run_id(project_id)
        documents = await self.get_documents(project_id)

        sheet_rows = await self._run(
            "MATCH (d:Document {project_id: $pid})-[:HAS_SHEET]->(s:Sheet) "
            "RETURN d.id AS document_id, s { .* } AS sheet ORDER BY s.page_number",
            pid=project_id,
        )
        verdict_filter = "WHERE $run_id IS NULL OR v.run_id = $run_id"
        asset_rows = await self._run(
            "MATCH (s:Sheet {project_id: $pid})-[:CONTAINS]->(a:PhysicalAsset) "
            "OPTIONAL MATCH (a)-[v]->(r:Regulation) "
            f"{verdict_filter} "
            "WITH s, a, collect(CASE WHEN v IS NULL THEN null ELSE {"
            "verdict: type(v), run_id: v.run_id, measured: v.measured, "
            "required: v.required, reason: v.reason, regulation_id: r.id, "
            "clause_id: r.clause_id, clause_page: r.page, clause_bbox: r.bbox, "
            "clause_document_id: r.document_id} END) AS verdicts "
            "RETURN s.id AS sheet_id, a { .* } AS asset, "
            "[x IN verdicts WHERE x IS NOT NULL] AS verdicts",
            pid=project_id,
            run_id=run_id,
        )
        assets_by_sheet: dict[str, list[dict]] = {}
        for row in asset_rows:
            asset = row["asset"]
            asset["measurements"] = json.loads(asset.get("measurements") or "{}")
            asset.pop("embedding", None)
            asset["verdicts"] = row["verdicts"]
            assets_by_sheet.setdefault(row["sheet_id"], []).append(asset)

        sheets = []
        for row in sheet_rows:
            sheet = row["sheet"]
            sheet["document_id"] = row["document_id"]
            sheet["assets"] = assets_by_sheet.get(sheet["id"], [])
            sheets.append(sheet)

        return {
            "project": projects[0]["p"],
            "documents": documents,
            "sheets": sheets,
            "clauses": await self.get_clauses(project_id),
        }
