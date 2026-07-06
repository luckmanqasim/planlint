"use client";

import { useEffect, useRef } from "react";
import type { Asset, Clause } from "@/lib/types";

interface Props {
  clauses: Clause[];
  selectedAsset: Asset | null;
}

/**
 * The semantic pane: the codebook's clause tree. When an asset is selected
 * on the plan, the clauses governing it are highlighted and the worst one
 * is scrolled into view — violation first.
 */
export default function CodePane({ clauses, selectedAsset }: Props) {
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  const verdictsByRegulation = new Map(
    (selectedAsset?.verdicts ?? []).map((v) => [v.regulation_id, v]),
  );

  useEffect(() => {
    if (!selectedAsset) return;
    const ranked = [...selectedAsset.verdicts].sort((a, b) => {
      const order = { VIOLATES: 0, NEEDS_REVIEW: 1, COMPLIES_WITH: 2 };
      return order[a.verdict] - order[b.verdict];
    });
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
      <p className="muted" style={{ padding: 20 }}>
        No codebook ingested yet — upload one and run verification.
      </p>
    );
  }

  return (
    <div>
      {clauses.map((clause) => {
        const verdict = verdictsByRegulation.get(clause.id);
        const classes = ["clause"];
        if (verdict?.verdict === "VIOLATES") classes.push("violated");
        else if (verdict) classes.push("highlight");
        return (
          <div
            key={clause.id}
            className={classes.join(" ")}
            ref={(el) => {
              refs.current[clause.id] = el;
            }}
          >
            <h4>
              {clause.clause_id} {clause.title}
            </h4>
            {clause.hierarchy_path && <div className="path">{clause.hierarchy_path}</div>}
            <div className="muted" style={{ whiteSpace: "pre-wrap" }}>
              {clause.text}
            </div>
            {verdict && (
              <div style={{ marginTop: 8 }}>
                <span className={`badge ${verdict.verdict}`}>{verdict.verdict}</span>
                {verdict.measured != null && (
                  <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
                    measured {verdict.measured}&Prime; · required {verdict.required}
                  </span>
                )}
                {verdict.reason && (
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    {verdict.reason}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
