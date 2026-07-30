"use client";

// Project workspace: uploads, verification runs, and the two-pane review
// surface (plan canvas ↔ clause pane). Owns the sheet/asset selection model
// that keeps the two panes in sync in both directions.

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import AssetIndex from "@/components/AssetIndex";
import AssetInspector from "@/components/AssetInspector";
import CodebookModal, { type CodebookView } from "@/components/CodebookModal";
import CodePane from "@/components/CodePane";
import ConfirmDialog, { type ConfirmRequest } from "@/components/ConfirmDialog";
import PlanViewer from "@/components/PlanViewer";
import RunProgress from "@/components/RunProgress";
import Toasts, { useToasts } from "@/components/Toasts";
import { api } from "@/lib/api";
import { useVerificationRun } from "@/lib/useVerificationRun";
import { verdictGlyph, worstBoxVerdict } from "@/lib/verdicts";
import type { Asset, Results, Sheet, Verdict } from "@/lib/types";

const VERDICT_KEYS = [
  "verdict:VIOLATES",
  "verdict:COMPLIES_WITH",
  "verdict:NEEDS_REVIEW",
  "verdict:UNCHECKED",
];

interface Selection {
  sheetId: string;
  assetId: string;
}

const KIND_ICON = { floorplan: "▤", codebook: "§" } as const;

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);
  const [results, setResults] = useState<Results | null>(null);
  const [sheetIndex, setSheetIndex] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scaleInput, setScaleInput] = useState("");
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [rightTab, setRightTab] = useState<"clauses" | "assets">("clauses");
  const [focusClauseId, setFocusClauseId] = useState<string | null>(null);
  const [codebookView, setCodebookView] = useState<CodebookView | null>(null);
  const { toasts, push: pushToast } = useToasts();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadKindRef = useRef<"floorplan" | "codebook">("floorplan");

  const refresh = useCallback(() => {
    api
      .results(projectId)
      .then((data) => {
        setResults(data);
        setError(null);
        // Keep the selection only if that asset still exists; clamp the
        // sheet index in case sheets shrank (document deleted).
        setSelection((prev) =>
          prev &&
          data.sheets.some(
            (s) => s.id === prev.sheetId && s.assets.some((a) => a.id === prev.assetId),
          )
            ? prev
            : null,
        );
        setSheetIndex((prev) => Math.max(0, Math.min(prev, data.sheets.length - 1)));
      })
      .catch((e) => setError(String(e)));
  }, [projectId]);

  useEffect(refresh, [refresh]);

  const { events, running, runError, start: verify } = useVerificationRun(projectId, refresh);

  const sheets = useMemo(() => results?.sheets ?? [], [results]);
  const sheet: Sheet | null = sheets[sheetIndex] ?? null;

  const selectedAsset: Asset | null = useMemo(() => {
    if (!selection) return null;
    return (
      sheets
        .find((s) => s.id === selection.sheetId)
        ?.assets.find((a) => a.id === selection.assetId) ?? null
    );
  }, [selection, sheets]);

  function setSelectedAsset(sheet: Sheet | null, asset: Asset | null) {
    if (!sheet || !asset) {
      console.log("[planlint] selected box cleared");
      setSelection(null);
      return;
    }
    console.log("[planlint] selected box", {
      sheetId: sheet.id,
      page: sheet.page_number + 1,
      assetId: asset.id,
      label: asset.label,
      type: asset.type,
      bbox: asset.bbox,
      verdicts: asset.verdicts,
    });
    setSelection({ sheetId: sheet.id, assetId: asset.id });
  }

  const summary = useMemo(() => {
    const counts = { VIOLATES: 0, COMPLIES_WITH: 0, NEEDS_REVIEW: 0 };
    for (const s of sheets) {
      for (const a of s.assets) {
        const worst = worstBoxVerdict(a.verdicts);
        if (worst) counts[worst] += 1;
      }
    }
    return counts;
  }, [sheets]);

  function switchSheet(index: number) {
    setSheetIndex(index);
    setSelection(null); // a selection from another sheet would be invisible
  }

  function selectOnCurrentSheet(asset: Asset | null) {
    setSelectedAsset(sheet, asset);
  }

  const toggleHidden = useCallback((key: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // Summary chip → show only this verdict (hide the others); click again clears.
  function onlyVerdict(v: Verdict) {
    const keep = `verdict:${v}`;
    const others = VERDICT_KEYS.filter((k) => k !== keep);
    const alreadyOnly = others.every((k) => hidden.has(k)) && !hidden.has(keep);
    setHidden(alreadyOnly ? new Set() : new Set(others));
  }

  function onSheetKeys(event: React.KeyboardEvent) {
    if (event.key === "ArrowLeft" && sheetIndex > 0) switchSheet(sheetIndex - 1);
    if (event.key === "ArrowRight" && sheetIndex < sheets.length - 1)
      switchSheet(sheetIndex + 1);
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
      pushToast(`Uploaded ${file.name} — run verification to analyze it`);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  function requestDeleteDocument(documentId: string, filename: string, kind: string) {
    setConfirm({
      title: `Delete ${filename}?`,
      body:
        kind === "codebook"
          ? "Its clauses, extracted constraints, and every verdict against them will be removed."
          : "Its sheets, detected assets, and their verdicts will be removed.",
      onConfirm: async () => {
        try {
          await api.deleteDocument(documentId);
          pushToast(`Deleted ${filename}`);
          refresh();
        } catch (e) {
          setError(String(e));
        }
      },
    });
  }

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

  const documents = results?.documents ?? [];

  return (
    <main className="flex h-screen flex-col">
      {/* Topbar */}
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-edge bg-surface-1 px-4">
        <Link
          href="/"
          className="shrink-0 text-ink-dim hover:text-ink"
          aria-label="Back to projects"
        >
          ←
        </Link>
        <nav className="min-w-0 text-ink-dim">
          <Link href="/" className="hover:text-ink">
            Projects
          </Link>
          <span className="mx-1.5">/</span>
          <span className="font-medium text-ink">
            {results?.project.name ?? projectId}
          </span>
        </nav>
        <div className="flex-1" />
        {summary.VIOLATES > 0 && (
          <button
            onClick={() => onlyVerdict("VIOLATES")}
            title="Show only violations"
            className="rounded-full bg-fail/15 px-2.5 py-1 text-xs font-medium text-fail hover:bg-fail/25"
          >
            {verdictGlyph("VIOLATES")} {summary.VIOLATES} violation
            {summary.VIOLATES === 1 ? "" : "s"}
          </button>
        )}
        {summary.COMPLIES_WITH > 0 && (
          <button
            onClick={() => onlyVerdict("COMPLIES_WITH")}
            title="Show only passes"
            className="rounded-full bg-pass/15 px-2.5 py-1 text-xs font-medium text-pass hover:bg-pass/25"
          >
            {verdictGlyph("COMPLIES_WITH")} {summary.COMPLIES_WITH} pass
          </button>
        )}
        {summary.NEEDS_REVIEW > 0 && (
          <button
            onClick={() => onlyVerdict("NEEDS_REVIEW")}
            title="Show only needs-review"
            className="rounded-full bg-review/15 px-2.5 py-1 text-xs font-medium text-review hover:bg-review/25"
          >
            {verdictGlyph("NEEDS_REVIEW")} {summary.NEEDS_REVIEW} review
          </button>
        )}
        <button
          onClick={() => pickFile("floorplan")}
          className="rounded-lg border border-edge bg-surface-2 px-3 py-1.5 hover:bg-edge"
        >
          + Floor plan
        </button>
        <button
          onClick={() => pickFile("codebook")}
          className="rounded-lg border border-edge bg-surface-2 px-3 py-1.5 hover:bg-edge"
        >
          + Codebook
        </button>
        <button
          onClick={verify}
          disabled={running || documents.length === 0}
          className="flex items-center gap-2 rounded-lg bg-accent px-3.5 py-1.5 font-medium text-surface-0 hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running && (
            <span
              aria-hidden
              className="size-3.5 animate-spin rounded-full border-2 border-surface-0/40 border-t-surface-0"
            />
          )}
          {running ? "Verifying…" : "Run verification"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={onFileChosen}
          aria-label="Upload PDF document"
        />
      </header>

      {/* Documents strip */}
      {documents.length > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-edge bg-surface-1 px-4 py-2">
          {documents.map((doc) => (
            <span
              key={doc.id}
              className="inline-flex items-center gap-2 rounded-full border border-edge bg-surface-2 py-1 pr-1.5 pl-3 text-xs"
            >
              <span aria-hidden className="text-ink-dim">
                {KIND_ICON[doc.kind]}
              </span>
              {doc.filename}
              <span
                title={doc.ingested ? "Ingested" : "Awaiting ingestion"}
                className={`size-1.5 rounded-full ${doc.ingested ? "bg-pass" : "bg-ink-dim"}`}
              />
              <button
                aria-label={`Delete ${doc.filename}`}
                onClick={() => requestDeleteDocument(doc.id, doc.filename, doc.kind)}
                className="rounded-full px-1.5 py-0.5 text-ink-dim hover:bg-fail/20 hover:text-fail"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Persistent error banners */}
      {(error || runError) && (
        <div className="shrink-0 border-b border-fail/40 bg-fail/10 px-4 py-2 text-fail">
          {error ?? runError}
        </div>
      )}
      <RunProgress events={events} />

      {/* Scale entry */}
      {sheet && sheet.scale_in_per_point == null && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-review/40 bg-review/10 px-4 py-2">
          <span className="text-xs font-medium text-review">
            ? Drawing scale not detected — dimensional checks need it
          </span>
          <input
            type="text"
            aria-label="Drawing scale"
            placeholder={'e.g. 1/4" = 1\'-0" or 1:50'}
            value={scaleInput}
            onChange={(e) => setScaleInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitScale()}
            className="w-64 rounded-lg border border-edge bg-surface-2 px-2.5 py-1 placeholder:text-ink-dim/60 focus:outline-2 focus:outline-accent"
          />
          <button
            onClick={submitScale}
            className="rounded-lg border border-edge bg-surface-2 px-3 py-1 hover:bg-edge"
          >
            Apply scale &amp; re-verify
          </button>
        </div>
      )}

      {/* Workspace */}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="overflow-y-auto border-r border-edge bg-surface-1">
          {sheets.length > 1 && (
            <div
              role="tablist"
              aria-label="Sheets"
              onKeyDown={onSheetKeys}
              className="sticky top-0 z-10 flex gap-1 border-b border-edge bg-surface-1/95 px-3 py-2 backdrop-blur"
            >
              {sheets.map((s, i) => {
                const fails = s.assets.filter((a) =>
                  a.verdicts.some((v) => v.verdict === "VIOLATES"),
                ).length;
                return (
                  <button
                    key={s.id}
                    role="tab"
                    aria-selected={i === sheetIndex}
                    onClick={() => switchSheet(i)}
                    className={`flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs ${
                      i === sheetIndex
                        ? "bg-surface-2 font-medium text-ink"
                        : "text-ink-dim hover:bg-surface-2/60 hover:text-ink"
                    }`}
                  >
                    Sheet {s.page_number + 1}
                    {fails > 0 && (
                      <span className="rounded-full bg-fail/20 px-1.5 text-[10px] font-semibold text-fail">
                        {fails}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {sheet ? (
            <PlanViewer
              sheet={sheet}
              selectedAssetId={selectedAsset?.id ?? null}
              onSelectAsset={selectOnCurrentSheet}
              hidden={hidden}
              onToggleHidden={toggleHidden}
            />
          ) : documents.length === 0 ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="w-full max-w-md rounded-xl border border-dashed border-edge p-8 text-center">
                <h2 className="text-base font-semibold">Empty project</h2>
                <p className="mt-1 text-ink-dim">
                  Add a floor plan and a codebook, then run verification.
                </p>
                <div className="mt-5 grid grid-cols-2 gap-3">
                  <button
                    onClick={() => pickFile("floorplan")}
                    className="rounded-xl border border-edge bg-surface-2 px-4 py-6 hover:border-accent hover:bg-edge"
                  >
                    <div aria-hidden className="text-2xl">
                      {KIND_ICON.floorplan}
                    </div>
                    Add a floor plan
                  </button>
                  <button
                    onClick={() => pickFile("codebook")}
                    className="rounded-xl border border-edge bg-surface-2 px-4 py-6 hover:border-accent hover:bg-edge"
                  >
                    <div aria-hidden className="text-2xl">
                      {KIND_ICON.codebook}
                    </div>
                    Add a codebook
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <p className="p-6 text-ink-dim">
              No floor plan ingested yet — run verification to analyze uploaded
              documents.
            </p>
          )}

        </div>

        <div className="flex min-h-0 flex-col overflow-hidden bg-surface-1">
          <div className="max-h-[45%] shrink-0 overflow-y-auto">
            <AssetInspector
              asset={selectedAsset}
              onFocusClause={(regId) => {
                setRightTab("clauses");
                setFocusClauseId(regId);
              }}
              onViewClause={setCodebookView}
            />
          </div>
          <div
            role="tablist"
            aria-label="Review views"
            className="flex shrink-0 gap-1 border-b border-edge px-3 py-2"
          >
            {(["clauses", "assets"] as const).map((tab) => (
              <button
                key={tab}
                role="tab"
                aria-selected={rightTab === tab}
                onClick={() => setRightTab(tab)}
                className={`rounded-lg px-3 py-1 text-xs capitalize ${
                  rightTab === tab
                    ? "bg-surface-2 font-medium text-ink"
                    : "text-ink-dim hover:bg-surface-2/60 hover:text-ink"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {rightTab === "clauses" ? (
              <CodePane
                clauses={results?.clauses ?? []}
                documents={documents}
                sheet={sheet}
                selectedAsset={selectedAsset}
                onSelectAsset={selectOnCurrentSheet}
                focusClauseId={focusClauseId}
                onViewClause={setCodebookView}
              />
            ) : (
              <AssetIndex
                assets={sheet?.assets ?? []}
                hidden={hidden}
                selectedAssetId={selectedAsset?.id ?? null}
                onSelect={selectOnCurrentSheet}
              />
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
      <CodebookModal view={codebookView} onClose={() => setCodebookView(null)} />
      <Toasts toasts={toasts} />
    </main>
  );
}
