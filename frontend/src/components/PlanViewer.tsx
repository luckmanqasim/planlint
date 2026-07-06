"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Asset, Sheet, Verdict } from "@/lib/types";

const COLORS: Record<Verdict, string> = {
  COMPLIES_WITH: "#2ecc71",
  VIOLATES: "#e74c3c",
  NEEDS_REVIEW: "#f39c12",
};

/** Box color: red on any violation; green when at least one check passed
 * and none failed; amber only when nothing was machine-checkable. */
function assetVerdict(asset: Asset): Verdict | null {
  const verdicts = asset.verdicts.map((v) => v.verdict);
  if (verdicts.includes("VIOLATES")) return "VIOLATES";
  if (verdicts.includes("COMPLIES_WITH")) return "COMPLIES_WITH";
  if (verdicts.includes("NEEDS_REVIEW")) return "NEEDS_REVIEW";
  return null;
}

interface Props {
  sheet: Sheet;
  selectedAssetId: string | null;
  onSelectAsset: (asset: Asset | null) => void;
}

export default function PlanViewer({ sheet, selectedAssetId, onSelectAsset }: Props) {
  const pdfCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [renderScale, setRenderScale] = useState(1);
  const [renderError, setRenderError] = useState<string | null>(null);

  // Render the PDF page once per sheet.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();
        const pdf = await pdfjs.getDocument(api.pdfUrl(sheet.document_id)).promise;
        if (cancelled) return;
        const page = await pdf.getPage(sheet.page_number + 1);
        const containerWidth = containerRef.current?.clientWidth ?? 800;
        const scale = Math.min((containerWidth - 24) / sheet.width, 2.5);
        const viewport = page.getViewport({ scale });
        const canvas = pdfCanvasRef.current!;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const overlay = overlayRef.current!;
        overlay.width = viewport.width;
        overlay.height = viewport.height;
        await page.render({
          canvasContext: canvas.getContext("2d")!,
          viewport,
        }).promise;
        if (!cancelled) setRenderScale(scale);
      } catch (error) {
        if (!cancelled) setRenderError(String(error));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sheet.id, sheet.document_id, sheet.page_number, sheet.width]);

  // Draw bounding boxes whenever verdicts/selection change.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const ctx = overlay.getContext("2d")!;
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    for (const asset of sheet.assets) {
      const verdict = assetVerdict(asset);
      const color = verdict ? COLORS[verdict] : "#9aa3b2";
      const [x0, y0, x1, y1] = asset.bbox;
      const pad = 6;
      const x = (x0 - pad) * renderScale;
      const y = (y0 - pad) * renderScale;
      const w = (x1 - x0 + pad * 2) * renderScale;
      const h = (y1 - y0 + pad * 2) * renderScale;
      const selected = asset.id === selectedAssetId;
      ctx.lineWidth = selected ? 3 : 1.5;
      ctx.strokeStyle = color;
      ctx.fillStyle = color + (selected ? "33" : "1a");
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
      if (asset.label) {
        ctx.font = `${selected ? "bold " : ""}${11}px ui-sans-serif`;
        ctx.fillStyle = color;
        ctx.fillText(asset.label, x, y - 4);
      }
    }
  }, [sheet.assets, selectedAssetId, renderScale]);

  function handleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const overlay = overlayRef.current!;
    const rect = overlay.getBoundingClientRect();
    const x = (event.clientX - rect.left) / renderScale;
    const y = (event.clientY - rect.top) / renderScale;
    const pad = 6;
    const hit = sheet.assets.find(
      (asset) =>
        x >= asset.bbox[0] - pad &&
        x <= asset.bbox[2] + pad &&
        y >= asset.bbox[1] - pad &&
        y <= asset.bbox[3] + pad,
    );
    onSelectAsset(hit ?? null);
  }

  return (
    <div ref={containerRef} style={{ padding: 12 }}>
      {renderError && <p style={{ color: "var(--red)" }}>PDF render failed: {renderError}</p>}
      <div style={{ position: "relative", display: "inline-block" }}>
        <canvas ref={pdfCanvasRef} style={{ display: "block", borderRadius: 6 }} />
        <canvas
          ref={overlayRef}
          onClick={handleClick}
          style={{ position: "absolute", inset: 0, cursor: "crosshair" }}
        />
      </div>
    </div>
  );
}
