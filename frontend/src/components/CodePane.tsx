"use client";

import { useEffect, useMemo, useRef } from "react";

import { formatInches, verdictGlyph, verdictLabel, VERDICT_SEVERITY } from "@/lib/verdicts";
import type { Asset, Clause, Doc, Sheet, VerdictEdge } from "@/lib/types";

interface Props {
  clauses: Clause[];
  documents: Doc[];
  sheet: Sheet | null;
  selectedAsset: Asset | null;
  onSelectAsset: (asset: Asset | null) => void;
}

const VERDICT_CARD: Record<VerdictEdge["verdict"], string> = {
  VIOLATES: "border-l-fail bg-fail/10",
  COMPLIES_WITH: "border-l-pass bg-pass/10",
  NEEDS_REVIEW: "border-l-review bg-review/10",
};

const VERDICT_BADGE: Record<VerdictEdge["verdict"], string> = {
  VIOLATES: "bg-fail/15 text-fail",
  COMPLIES_WITH: "bg-pass/15 text-pass",
  NEEDS_REVIEW: "bg-review/15 text-review",
};

/**
 * The semantic pane: the codebook's clause list (grouped per codebook when
 * several are loaded). Selecting an asset on the plan highlights its
 * governing clauses and scrolls to the worst one; clauses governing any
 * asset on the current sheet are clickable and select that asset back on
 * the canvas.
 */
export default function CodePane({
  clauses,
  documents,
  sheet,
  selectedAsset,
  onSelectAsset,
}: Props) {
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  const selectedVerdicts = useMemo(
    () => new Map((selectedAsset?.verdicts ?? []).map((v) => [v.regulation_id, v])),
    [selectedAsset],
  );

  // regulation_id -> the (worst) asset on the current sheet it governs.
  const assetsByRegulation = useMemo(() => {
    const map = new Map<string, { asset: Asset; verdict: VerdictEdge }>();
    for (const asset of sheet?.assets ?? []) {
      for (const verdict of asset.verdicts) {
        const existing = map.get(verdict.regulation_id);
        if (
          !existing ||
          VERDICT_SEVERITY[verdict.verdict] < VERDICT_SEVERITY[existing.verdict.verdict]
        ) {
          map.set(verdict.regulation_id, { asset, verdict });
        }
      }
    }
    return map;
  }, [sheet]);

  const codebooks = useMemo(
    () => documents.filter((d) => d.kind === "codebook"),
    [documents],
  );

  const groups = useMemo(() => {
    if (codebooks.length <= 1) return [{ codebook: null as Doc | null, clauses }];
    return codebooks
      .map((codebook) => ({
        codebook: codebook as Doc | null,
        clauses: clauses.filter((c) => c.document_id === codebook.id),
      }))
      .filter((group) => group.clauses.length > 0);
  }, [codebooks, clauses]);

  useEffect(() => {
    if (!selectedAsset) return;
    const ranked = [...selectedAsset.verdicts].sort(
      (a, b) => VERDICT_SEVERITY[a.verdict] - VERDICT_SEVERITY[b.verdict],
    );
    const target = ranked[0];
    if (target) {
      refs.current[target.regulation_id]?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
  }, [selectedAsset]);

  if (clauses.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="max-w-xs text-center text-ink-dim">
          No codebook ingested yet — upload one and run verification to see
          governing clauses here.
        </p>
      </div>
    );
  }

  return (
    <div>
      {groups.map((group, groupIndex) => (
        <section key={group.codebook?.id ?? "all"}>
          {group.codebook && (
            <h3 className="sticky top-0 z-10 border-b border-edge bg-surface-1/95 px-4 py-2 font-medium backdrop-blur">
              {group.codebook.filename}
            </h3>
          )}
          {group.clauses.map((clause) => {
            const verdict = selectedVerdicts.get(clause.id);
            const governed = assetsByRegulation.get(clause.id);
            const clickable = governed !== undefined;
            return (
              <div
                key={`${groupIndex}-${clause.id}`}
                ref={(el) => {
                  refs.current[clause.id] = el;
                }}
                role={clickable ? "button" : undefined}
                tabIndex={clickable ? 0 : undefined}
                onClick={clickable ? () => onSelectAsset(governed.asset) : undefined}
                onKeyDown={
                  clickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelectAsset(governed.asset);
                        }
                      }
                    : undefined
                }
                className={`border-b border-edge px-4 py-3 scroll-mt-10 [content-visibility:auto] [contain-intrinsic-size:auto_120px] ${
                  verdict
                    ? `border-l-2 ${VERDICT_CARD[verdict.verdict]}`
                    : "border-l-2 border-l-transparent"
                } ${clickable ? "cursor-pointer hover:bg-surface-2 focus:outline-2 focus:-outline-offset-2 focus:outline-accent" : ""}`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <h4 className="font-medium">
                    {clause.clause_id} {clause.title}
                  </h4>
                  {!verdict && governed && (
                    <span className="shrink-0 rounded bg-surface-2 px-1.5 py-0.5 text-xs text-ink-dim">
                      → {governed.asset.label || governed.asset.id}
                    </span>
                  )}
                </div>
                {clause.hierarchy_path && (
                  <div className="mt-0.5 text-xs text-ink-dim">{clause.hierarchy_path}</div>
                )}
                <div className="mt-1.5 text-ink-dim whitespace-pre-wrap">{clause.text}</div>
                {verdict && (
                  <div className="mt-2.5">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${VERDICT_BADGE[verdict.verdict]}`}
                    >
                      {verdictGlyph(verdict.verdict)} {verdictLabel(verdict.verdict)}
                    </span>
                    {verdict.measured != null && (
                      <span className="ml-2 text-xs text-ink-dim">
                        measured {formatInches(verdict.measured)} · required {verdict.required}
                      </span>
                    )}
                    {verdict.reason && (
                      <div className="mt-1 text-xs text-ink-dim">{verdict.reason}</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}
