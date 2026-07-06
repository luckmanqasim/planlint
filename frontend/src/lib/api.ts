import type { ProjectSummary, Results, RunEvent } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
}

export const api = {
  listProjects: () =>
    fetch(`${API_URL}/projects`).then((r) => json<ProjectSummary[]>(r)),

  createProject: (name: string) =>
    fetch(`${API_URL}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then((r) => json<{ id: string }>(r)),

  createSampleProject: () =>
    fetch(`${API_URL}/projects/sample`, { method: "POST" }).then((r) =>
      json<{ id: string }>(r),
    ),

  uploadDocument: (projectId: string, kind: "floorplan" | "codebook", file: File) => {
    const body = new FormData();
    body.append("file", file);
    return fetch(
      `${API_URL}/projects/${projectId}/documents?kind=${kind}`,
      { method: "POST", body },
    ).then((r) => json<{ id: string }>(r));
  },

  startVerification: (projectId: string) =>
    fetch(`${API_URL}/projects/${projectId}/verify`, { method: "POST" }).then(
      (r) => json<{ run_id: string }>(r),
    ),

  results: (projectId: string) =>
    fetch(`${API_URL}/projects/${projectId}/results`).then((r) =>
      json<Results>(r),
    ),

  setDocumentScale: (documentId: string, scaleText: string) =>
    fetch(`${API_URL}/documents/${documentId}/scale`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scale_text: scaleText }),
    }).then((r) => json<{ status: string }>(r)),

  pdfUrl: (documentId: string) => `${API_URL}/documents/${documentId}/pdf`,

  /** Subscribe to run progress. Returns an unsubscribe function. */
  subscribeToRun(runId: string, onEvent: (event: RunEvent) => void, onDone: () => void) {
    const source = new EventSource(`${API_URL}/runs/${runId}/events`);
    source.addEventListener("progress", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as RunEvent;
      onEvent(event);
      if (event.stage === "done" || event.stage === "error") {
        source.close();
        onDone();
      }
    });
    source.onerror = () => {
      source.close();
      onDone();
    };
    return () => source.close();
  },
};
