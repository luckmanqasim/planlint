"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import ConfirmDialog, { type ConfirmRequest } from "@/components/ConfirmDialog";
import Toasts, { useToasts } from "@/components/Toasts";
import { api } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";

function relativeDate(iso?: string): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString();
}

export default function HomePage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<"create" | "sample" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const { toasts, push: pushToast } = useToasts();

  const refresh = useCallback(() => {
    api
      .listProjects()
      .then((data) => {
        setProjects(data);
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(refresh, [refresh]);

  async function createProject() {
    if (!name.trim()) return;
    setBusy("create");
    try {
      const project = await api.createProject(name.trim());
      router.push(`/project/${project.id}`);
    } catch (e) {
      setError(String(e));
      setBusy(null);
    }
  }

  async function loadSample() {
    setBusy("sample");
    try {
      const project = await api.createSampleProject();
      router.push(`/project/${project.id}`);
    } catch (e) {
      setError(String(e));
      setBusy(null);
    }
  }

  function requestDelete(project: ProjectSummary) {
    setConfirm({
      title: `Delete “${project.name}”?`,
      body: "All documents, verification results, and uploaded PDFs will be permanently removed.",
      onConfirm: async () => {
        setProjects((prev) => prev.filter((p) => p.id !== project.id)); // optimistic
        try {
          await api.deleteProject(project.id);
          pushToast(`Deleted ${project.name}`);
        } catch (e) {
          setError(String(e));
          refresh(); // restore the card if the delete failed
        }
      },
    });
  }

  const spinner = (
    <span
      aria-hidden
      className="size-3.5 animate-spin rounded-full border-2 border-surface-0/40 border-t-surface-0"
    />
  );

  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">PlanLint</h1>
      <p className="mt-1 mb-8 text-ink-dim">
        Lint floor plans against building codes — physical geometry bound to
        legal constraints, compliance proven as a graph.
      </p>

      {error && (
        <div className="mb-6 rounded-xl border border-fail/40 bg-fail/10 px-4 py-3 text-fail">
          {error}
        </div>
      )}

      <div className="mb-10 rounded-xl border border-edge bg-surface-1 p-4">
        <div className="flex flex-wrap gap-2.5">
          <input
            type="text"
            aria-label="New project name"
            placeholder="New project name…"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createProject()}
            className="min-w-48 flex-1 rounded-lg border border-edge bg-surface-2 px-3 py-2 placeholder:text-ink-dim/60 focus:outline-2 focus:outline-accent"
          />
          <button
            onClick={createProject}
            disabled={busy !== null || !name.trim()}
            className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 font-medium text-surface-0 hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === "create" && spinner}
            Create project
          </button>
          <button
            onClick={loadSample}
            disabled={busy !== null}
            className="flex items-center gap-2 rounded-lg border border-edge bg-surface-2 px-4 py-2 hover:bg-edge disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === "sample" && (
              <span
                aria-hidden
                className="size-3.5 animate-spin rounded-full border-2 border-ink/30 border-t-ink"
              />
            )}
            Load sample project
          </button>
        </div>
        <p className="mt-3 text-xs text-ink-dim">
          The sample bundles a vector floor plan and an excerpt of the 2010 ADA
          Standards (public domain) — including one door that violates the 32″
          clear-width rule.
        </p>
      </div>

      <h2 className="mb-3 font-medium">Projects</h2>
      {projects.length === 0 ? (
        <div className="rounded-xl border border-dashed border-edge p-10 text-center text-ink-dim">
          No projects yet — create one or load the sample.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {projects.map((project) => (
            <div
              key={project.id}
              className="relative rounded-xl border border-edge bg-surface-1 transition-colors hover:border-accent/60"
            >
              <Link href={`/project/${project.id}`} className="block p-4 pr-12">
                <span className="block truncate font-medium">{project.name}</span>
                <span className="mt-1 block text-xs text-ink-dim">
                  {project.document_count} document
                  {project.document_count === 1 ? "" : "s"}
                  {relativeDate(project.created_at) && (
                    <> · created {relativeDate(project.created_at)}</>
                  )}
                </span>
              </Link>
              <button
                aria-label={`Delete project ${project.name}`}
                onClick={() => requestDelete(project)}
                className="absolute top-3 right-3 rounded-lg p-1.5 text-ink-dim hover:bg-fail/15 hover:text-fail focus:outline-2 focus:outline-accent"
              >
                <svg
                  aria-hidden
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                >
                  <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
      <Toasts toasts={toasts} />
    </main>
  );
}
