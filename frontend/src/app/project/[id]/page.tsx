"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import CodePane from "@/components/CodePane";
import PlanViewer from "@/components/PlanViewer";
import RunProgress from "@/components/RunProgress";
import { api } from "@/lib/api";
import type { Asset, Results, RunEvent } from "@/lib/types";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);
  const [results, setResults] = useState<Results | null>(null);
  const [sheetIndex, setSheetIndex] = useState(0);
  const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scaleInput, setScaleInput] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadKindRef = useRef<"floorplan" | "codebook">("floorplan");

  const refresh = useCallback(() => {
    api
      .results(projectId)
      .then((data) => {
        setResults(data);
        setSelectedAsset(null);
      })
      .catch((e) => setError(String(e)));
  }, [projectId]);

  useEffect(refresh, [refresh]);

  async function verify() {
    setRunning(true);
    setEvents([]);
    try {
      const { run_id } = await api.startVerification(projectId);
      api.subscribeToRun(
        run_id,
        (event) => setEvents((previous) => [...previous, event]),
        () => {
          setRunning(false);
          refresh();
        },
      );
    } catch (e) {
      setError(String(e));
      setRunning(false);
    }
  }

  function pickFile(kind: "floorplan" | "codebook") {
    uploadKindRef.current = kind;
    fileInputRef.current?.click();
  }

  async function onFileChosen(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      await api.uploadDocument(projectId, uploadKindRef.current, file);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  const sheets = results?.sheets ?? [];
  const sheet = sheets[sheetIndex] ?? null;
  const violations =
    sheets.flatMap((s) =>
      s.assets
        .filter((a) => a.verdicts.some((v) => v.verdict === "VIOLATES"))
        .map((a) => ({ sheet: s, asset: a })),
    ) ?? [];

  async function submitScale() {
    if (!sheet || !scaleInput.trim()) return;
    try {
      await api.setDocumentScale(sheet.document_id, scaleInput.trim());
      setScaleInput("");
      setError(null);
      await verify(); // re-ingest + re-verify with the manual scale
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <main>
      <div className="topbar">
        <a href="/">← Projects</a>
        <strong>{results?.project.name ?? projectId}</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {results?.documents.map((d) => d.filename).join(" · ")}
        </span>
        <div style={{ flex: 1 }} />
        {violations.length > 0 && (
          <span className="badge VIOLATES">{violations.length} violation{violations.length === 1 ? "" : "s"}</span>
        )}
        {sheets.length > 1 && (
          <select
            value={sheetIndex}
            onChange={(e) => setSheetIndex(Number(e.target.value))}
            style={{ background: "var(--panel-2)", color: "var(--text)", padding: 6, borderRadius: 6 }}
          >
            {sheets.map((s, i) => (
              <option key={s.id} value={i}>
                Sheet {s.page_number + 1}
              </option>
            ))}
          </select>
        )}
        <button className="secondary" onClick={() => pickFile("floorplan")}>
          + Floor plan
        </button>
        <button className="secondary" onClick={() => pickFile("codebook")}>
          + Codebook
        </button>
        <button onClick={verify} disabled={running}>
          {running ? "Verifying…" : "Run verification"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          style={{ display: "none" }}
          onChange={onFileChosen}
        />
      </div>

      {error && (
        <div style={{ padding: "8px 16px", color: "var(--red)" }}>{error}</div>
      )}
      <RunProgress events={events} />

      {sheet && sheet.scale_in_per_point == null && (
        <div style={{ padding: "8px 16px", display: "flex", gap: 8, alignItems: "center" }}>
          <span className="badge NEEDS_REVIEW">scale not detected</span>
          <input
            type="text"
            placeholder={'Enter drawing scale, e.g. 1/4" = 1\'-0" or 1:50'}
            value={scaleInput}
            onChange={(e) => setScaleInput(e.target.value)}
            style={{ width: 280 }}
          />
          <button className="secondary" onClick={submitScale}>
            Apply scale &amp; re-verify
          </button>
        </div>
      )}

      <div className="workspace">
        <div className="pane">
          {sheet ? (
            <PlanViewer
              sheet={sheet}
              selectedAssetId={selectedAsset?.id ?? null}
              onSelectAsset={setSelectedAsset}
            />
          ) : (
            <p className="muted" style={{ padding: 20 }}>
              No floor plan ingested yet — upload one and run verification.
            </p>
          )}
          {violations.length > 0 && (
            <div style={{ padding: "0 16px 16px" }}>
              <h4 style={{ margin: "8px 0" }}>Violations</h4>
              {violations.map(({ sheet: s, asset }) => {
                const violation = asset.verdicts.find((v) => v.verdict === "VIOLATES")!;
                return (
                  <div
                    key={asset.id}
                    className="card"
                    style={{ marginBottom: 8, cursor: "pointer", borderColor: "var(--red)" }}
                    onClick={() => {
                      setSheetIndex(sheets.indexOf(s));
                      setSelectedAsset(asset);
                    }}
                  >
                    <strong>{asset.label || asset.id}</strong>{" "}
                    <span className="badge VIOLATES">{violation.clause_id}</span>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {violation.reason}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <div className="pane">
          <CodePane clauses={results?.clauses ?? []} selectedAsset={selectedAsset} />
        </div>
      </div>
    </main>
  );
}
