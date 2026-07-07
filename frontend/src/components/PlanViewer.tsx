"use client";

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";

import { api } from "@/lib/api";
import { verdictGlyph, verdictColor, worstBoxVerdict } from "@/lib/verdicts";
import type { Asset, Sheet } from "@/lib/types";

const BOX_PAD = 6; // PDF points of breathing room around asset bboxes
const MAX_SCALE = 2.5;
const UNVERDICTED = "#98a1b3"; // --color-ink-dim; assets with no checks yet

interface Props {
  sheet: Sheet;
  selectedAssetId: string | null;
  onSelectAsset: (asset: Asset | null) => void;
}

function isRenderingCancelled(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "RenderingCancelledException"
  );
}

function hitTest(sheet: Sheet, x: number, y: number): Asset | undefined {
  return sheet.assets.find(
    (asset) =>
      x >= asset.bbox[0] - BOX_PAD &&
      x <= asset.bbox[2] + BOX_PAD &&
      y >= asset.bbox[1] - BOX_PAD &&
      y <= asset.bbox[3] + BOX_PAD,
  );
}

export default function PlanViewer({ sheet, selectedAssetId, onSelectAsset }: Props) {
  const pdfCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const pdfCacheRef = useRef<Map<string, PDFDocumentProxy>>(new Map());
  const renderTaskRef = useRef<RenderTask | null>(null);

  const [containerWidth, setContainerWidth] = useState(0);
  const [render, setRender] = useState<{ scale: number; dpr: number } | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  // Track pane width so the canvas reflows with the layout.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setContainerWidth(element.clientWidth));
    });
    observer.observe(element);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  // Destroy cached PDF proxies (and any in-flight render) on unmount.
  useEffect(() => {
    const cache = pdfCacheRef.current;
    return () => {
      renderTaskRef.current?.cancel();
      for (const pdf of cache.values()) void pdf.destroy();
      cache.clear();
    };
  }, []);

  // Render the PDF page. Re-runs on sheet switch and pane resize; in-flight
  // renders are cancelled so rapid switching can't corrupt the canvas. The
  // document proxy is cached per document — switching sheets within one
  // multi-page plan skips the download/parse entirely.
  useEffect(() => {
    if (containerWidth === 0) return; // wait for the first measurement
    let cancelled = false;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();
        let pdf = pdfCacheRef.current.get(sheet.document_id);
        if (!pdf) {
          pdf = await pdfjs.getDocument(api.pdfUrl(sheet.document_id)).promise;
          pdfCacheRef.current.set(sheet.document_id, pdf);
        }
        if (cancelled) return;
        const page = await pdf.getPage(sheet.page_number + 1);
        if (cancelled) return;

        const scale = Math.min((containerWidth - 24) / sheet.width, MAX_SCALE);
        const dpr = window.devicePixelRatio || 1;
        const viewport = page.getViewport({ scale: scale * dpr });
        const canvas = pdfCanvasRef.current;
        const overlay = overlayRef.current;
        if (!canvas || !overlay) return;
        for (const target of [canvas, overlay]) {
          target.width = viewport.width;
          target.height = viewport.height;
          target.style.width = `${viewport.width / dpr}px`;
          target.style.height = `${viewport.height / dpr}px`;
        }

        renderTaskRef.current?.cancel();
        const context = canvas.getContext("2d");
        if (!context) return;
        const task = page.render({ canvasContext: context, viewport });
        renderTaskRef.current = task;
        await task.promise;
        if (!cancelled) {
          setRender({ scale, dpr });
          setRenderError(null);
        }
      } catch (error) {
        if (isRenderingCancelled(error)) return;
        if (!cancelled) setRenderError(String(error));
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
    };
  }, [sheet.id, sheet.document_id, sheet.page_number, sheet.width, containerWidth]);

  // Draw verdict boxes: color plus glyph chip, never color alone.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay || !render) return;
    const ctx = overlay.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(render.dpr, 0, 0, render.dpr, 0, 0);
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    for (const asset of sheet.assets) {
      const verdict = worstBoxVerdict(asset.verdicts);
      const color = verdict ? verdictColor(verdict) : UNVERDICTED;
      const [x0, y0, x1, y1] = asset.bbox;
      const x = (x0 - BOX_PAD) * render.scale;
      const y = (y0 - BOX_PAD) * render.scale;
      const w = (x1 - x0 + BOX_PAD * 2) * render.scale;
      const h = (y1 - y0 + BOX_PAD * 2) * render.scale;
      const selected = asset.id === selectedAssetId;

      ctx.globalAlpha = selected ? 0.22 : 0.1;
      ctx.fillStyle = color;
      ctx.fillRect(x, y, w, h);
      ctx.globalAlpha = 1;
      ctx.lineWidth = selected ? 3 : 1.5;
      ctx.strokeStyle = color;
      ctx.strokeRect(x, y, w, h);

      // Chip above the box: verdict glyph + label on a filled background.
      const text = [verdict ? verdictGlyph(verdict) : "", asset.label]
        .filter(Boolean)
        .join(" ");
      if (text) {
        ctx.font = `${selected ? "600 " : ""}11px ui-sans-serif, system-ui`;
        const metrics = ctx.measureText(text);
        const chipH = 16;
        const chipY = Math.max(y - chipH - 2, 0);
        ctx.fillStyle = color;
        ctx.fillRect(x, chipY, metrics.width + 10, chipH);
        ctx.fillStyle = "#0b0d12"; // --color-surface-0: dark text on the chip
        ctx.fillText(text, x + 5, chipY + 12);
      }
    }
  }, [sheet.assets, selectedAssetId, render]);

  function toSheetCoords(event: React.MouseEvent<HTMLCanvasElement>) {
    const overlay = overlayRef.current;
    if (!overlay || !render) return null;
    const rect = overlay.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / render.scale,
      y: (event.clientY - rect.top) / render.scale,
    };
  }

  function handleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const point = toSheetCoords(event);
    if (!point) return;
    onSelectAsset(hitTest(sheet, point.x, point.y) ?? null);
  }

  function handleMove(event: React.MouseEvent<HTMLCanvasElement>) {
    const overlay = overlayRef.current;
    const point = toSheetCoords(event);
    if (!overlay || !point) return;
    overlay.style.cursor = hitTest(sheet, point.x, point.y) ? "pointer" : "default";
  }

  return (
    <div ref={containerRef} className="p-3">
      {renderError && (
        <p className="mb-2 rounded-lg border border-fail/40 bg-fail/10 px-3 py-2 text-fail">
          PDF render failed: {renderError}
        </p>
      )}
      <div className="relative inline-block">
        <canvas ref={pdfCanvasRef} className="block rounded-md bg-white" />
        <canvas
          ref={overlayRef}
          onClick={handleClick}
          onMouseMove={handleMove}
          className="absolute inset-0"
        />
      </div>
    </div>
  );
}
