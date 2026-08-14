"use client";

// The selected asset's detail card, pinned atop the right pane: what it is, how
// trustworthy its geometry is, its measurements, and the clauses that govern it.
// This is the asset→clause half of the cross-reference — each clause row focuses
// that clause in the Clauses list.

import {
  assetDisplayName,
  assetMeasurements,
  assetTypeLabel,
  sourceQuality,
} from "@/lib/assets";
import {
  formatInches,
  verdictBadgeClass,
  verdictGlyph,
  VERDICT_SEVERITY,
} from "@/lib/verdicts";
import type { CodebookView } from "@/components/CodebookModal";
import type { Asset } from "@/lib/types";

interface Props {
  asset: Asset | null;
  /** True once at least one verification run has completed for this project. */
  verified?: boolean;
  onFocusClause: (regulationId: string) => void;
  onViewClause?: (view: CodebookView) => void;
}

export default function AssetInspector({ asset, verified, onFocusClause, onViewClause }: Props) {
  if (!asset) {
    return (
      <div className="border-b border-edge px-4 py-3 text-xs text-ink-dim">
        Select an asset to see its measurements and governing clauses.
      </div>
    );
  }

  const quality = sourceQuality(asset.source);
  const measures = assetMeasurements(asset);
  const clauses = [...asset.verdicts].sort(
    (a, b) => VERDICT_SEVERITY[a.verdict] - VERDICT_SEVERITY[b.verdict],
  );

  return (
    <div className="border-b border-edge border-l-2 border-l-accent bg-surface-2 px-4 py-3 shadow-sm">
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-accent">
        Selected asset
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold text-ink">{assetDisplayName(asset)}</h3>
        <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-ink-dim">
          {assetTypeLabel(asset.type)}
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-xs ${
            quality.confirmed ? "bg-pass/15 text-pass" : "bg-review/15 text-review"
          }`}
        >
          {quality.label}
        </span>
      </div>

      {measures.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {measures.map((m) => (
            <span key={m.label} className="text-ink-dim">
              {m.label} <span className="font-mono text-ink">{m.value}</span>
            </span>
          ))}
        </div>
      )}

      {clauses.length > 0 ? (
        <div className="mt-3">
          <div className="mb-1 text-xs font-medium text-ink-dim">
            Governing clauses ({clauses.length})
          </div>
          <div className="flex flex-col gap-1">
            {clauses.map((v, i) => (
              <div key={i} className="flex items-stretch gap-1">
                <button
                  onClick={() => onFocusClause(v.regulation_id)}
                  className="flex flex-1 items-start gap-2 rounded border border-edge bg-surface-2/40 px-2 py-1.5 text-left text-xs hover:bg-surface-2 focus:outline-2 focus:-outline-offset-2 focus:outline-accent"
                >
                  <span
                    className={`shrink-0 rounded-full px-1.5 py-0.5 font-medium ${verdictBadgeClass(v.verdict)}`}
                    aria-hidden
                  >
                    {verdictGlyph(v.verdict)}
                  </span>
                  <span className="min-w-0">
                    <span className="font-mono text-ink">{v.clause_id}</span>
                    {v.measured != null && (
                      <span className="ml-1.5 text-ink-dim">
                        {formatInches(v.measured)} · {v.required}
                      </span>
                    )}
                    {v.reason && (
                      <span className="mt-0.5 block line-clamp-2 text-ink-dim">{v.reason}</span>
                    )}
                  </span>
                </button>
                {onViewClause && (
                  <button
                    onClick={() =>
                      onViewClause({
                        documentId: v.clause_document_id,
                        page: v.clause_page,
                        bbox: v.clause_bbox,
                      })
                    }
                    title="View in codebook"
                    aria-label="View in codebook"
                    className="shrink-0 rounded border border-edge bg-surface-2/40 px-1.5 text-xs text-ink-dim hover:bg-surface-2 hover:text-ink"
                  >
                    ⧉
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="mt-2 text-xs text-ink-dim">
          {verified
            ? "No applicable clauses found for this asset."
            : "No governing clauses yet \u2014 run verification against a codebook."}
        </p>
      )}
    </div>
  );
}
