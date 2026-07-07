import type { ProjectSummary, Results, RunEvent } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function assertOk(response: Response): Promise<void> {
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
}

export interface RunHandlers {
  onEvent: (event: RunEvent) => void;
  /** Run finished successfully (stage === "done"). */
  onDone: () => void;
  /** Run failed (stage === "error") or the connection dropped. */
  onError: (message: string) => void;
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

  deleteProject: (projectId: string) =>
    fetch(`${API_URL}/projects/${projectId}`, { method: "DELETE" }).then(assertOk),

  deleteDocument: (documentId: string) =>
    fetch(`${API_URL}/documents/${documentId}`, { method: "DELETE" }).then(assertOk),

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

  /** Subscribe to run progress. Returns an unsubscribe function. Exactly one
   * of onDone/onError fires, once; unsubscribing first prevents both. */
  subscribeToRun(runId: string, handlers: RunHandlers): () => void {
    const source = new EventSource(`${API_URL}/runs/${runId}/events`);
    let settled = false;

    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      source.close();
      fn();
    };

    source.addEventListener("progress", (message) => {
      let event: RunEvent;
      try {
        event = JSON.parse((message as MessageEvent).data) as RunEvent;
      } catch {
        settle(() => handlers.onError("received a malformed progress event"));
        return;
      }
      handlers.onEvent(event);
      if (event.stage === "done") {
        settle(handlers.onDone);
      } else if (event.stage === "error") {
        settle(() => handlers.onError(event.message));
      }
    });
    source.onerror = () => {
      settle(() => handlers.onError("connection to the server was lost"));
    };
    return () => {
      settled = true;
      source.close();
    };
  },
};
