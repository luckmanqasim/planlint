"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";

export default function HomePage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(refresh, [refresh]);

  async function createProject() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const project = await api.createProject(name.trim());
      router.push(`/project/${project.id}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function loadSample() {
    setBusy(true);
    try {
      const project = await api.createSampleProject();
      router.push(`/project/${project.id}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="container">
      <h1 style={{ marginBottom: 4 }}>PlanLint</h1>
      <p className="muted" style={{ marginBottom: 24 }}>
        Binds physical geometry in floor plans to legal constraints in building
        codes — and proves compliance as a graph.
      </p>

      {error && (
        <div className="card" style={{ borderColor: "var(--red)", marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{ display: "flex", gap: 10 }}>
          <input
            type="text"
            placeholder="New project name…"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createProject()}
            style={{ flex: 1 }}
          />
          <button onClick={createProject} disabled={busy || !name.trim()}>
            Create project
          </button>
          <button className="secondary" onClick={loadSample} disabled={busy}>
            Load sample project
          </button>
        </div>
        <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>
          The sample bundles a vector floor plan and an excerpt of the 2010 ADA
          Standards (public domain) — including one door that violates the 32&Prime;
          clear-width rule.
        </p>
      </div>

      <h3 style={{ marginBottom: 12 }}>Projects</h3>
      {projects.length === 0 && <p className="muted">No projects yet.</p>}
      <div style={{ display: "grid", gap: 10 }}>
        {projects.map((project) => (
          <a key={project.id} href={`/project/${project.id}`} className="card">
            <strong>{project.name}</strong>
            <span className="muted" style={{ marginLeft: 10 }}>
              {project.document_count} document{project.document_count === 1 ? "" : "s"}
            </span>
          </a>
        ))}
      </div>
    </main>
  );
}
