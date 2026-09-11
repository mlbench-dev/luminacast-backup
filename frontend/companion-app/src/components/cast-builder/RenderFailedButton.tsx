/**
 * RenderFailedButton — the "Render Failed — Retry" header control.
 *
 * Bug: while a render is in flight, RenderStatusPill's dropdown shows the
 * per-block failure reason(s) — but that dropdown only exists on the
 * "rendering" pill. Once the render fully fails, the header swaps to a bare
 * "Render Failed — Retry" button with nowhere to see WHY it failed. A user
 * who didn't catch the reason while the pill was briefly open (or who left
 * and came back later) had no way to view it again short of asking support
 * or re-checking devtools network calls.
 *
 * Fix: keep Retry a single click (unchanged), and add a small "why" info
 * button next to it that opens a click-to-open (not hover — hover has the
 * exact same "missed it" problem) popover with the render's error message,
 * mapped through the same friendly-language rules as the in-progress
 * per-block errors. Stays open until dismissed, so it's there whenever the
 * user goes looking for it, not just in the moment the render failed.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAnchoredPortal } from "./hooks/useAnchoredPortal";
import { friendlyBlockError } from "./RenderStatusPill";

export function RenderFailedButton({
  errorMessage,
  onRetry,
}: {
  errorMessage?: string;
  onRetry: () => void;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const pos = useAnchoredPortal(triggerRef, open, { align: "right", minWidth: 280 });

  const friendly = friendlyBlockError(errorMessage);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const panel = open && pos && createPortal(
    <div
      ref={panelRef}
      role="dialog"
      className="fixed z-[9999] rounded-lg border border-red-500/30 bg-neutral-950/95 backdrop-blur-md shadow-2xl p-3"
      style={{ top: pos.top, left: pos.left, width: pos.width }}
    >
      <div className="flex items-center gap-1.5 text-xs font-medium text-red-300 mb-1.5">
        <AlertCircle className="w-3.5 h-3.5 shrink-0" />
        Why the render failed
      </div>
      <p className="text-xs text-white/70 leading-snug" title={errorMessage || undefined}>
        {friendly}
      </p>
    </div>,
    document.body,
  );

  return (
    <div className="flex items-center border border-red-500/30 rounded-md bg-red-500/10 hover:bg-red-500/15 transition-colors">
      <Button
        onClick={onRetry}
        variant="ghost"
        className="text-red-300 hover:text-red-200 hover:bg-transparent h-9 rounded-r-none border-0"
        data-testid="finalize-render-btn"
      >
        <XCircle className="w-4 h-4 mr-2" />
        Render Failed — Retry
      </Button>
      <div className="w-px h-4 bg-red-500/20" />
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Why did it fail?"
        aria-label="Why did the render fail?"
        aria-expanded={open}
        className="h-9 w-9 flex items-center justify-center text-red-300/70 hover:text-red-200 transition-colors"
      >
        <AlertCircle className="w-4 h-4" />
      </button>
      {panel}
    </div>
  );
}
