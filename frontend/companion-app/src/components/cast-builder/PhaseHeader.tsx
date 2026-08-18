import { cn } from "@/lib/cn";
import { AlertTriangle, CheckCircle2 } from "lucide-react";

export type WizardPhase = "setup" | "generating_script" | "script" | "audio_generating" | "editor" | "ready";

const PHASES: { key: WizardPhase; label: string }[] = [
  { key: "setup", label: "Setup" },
  { key: "script", label: "Script" },
  { key: "audio_generating", label: "Audio" },
  { key: "editor", label: "Arrange" },
  { key: "ready", label: "Ready" },
];

interface PhaseHeaderProps {
  currentPhase: WizardPhase;
  castName?: string;
  onPhaseClick?: (phase: WizardPhase) => void;
  actions?: React.ReactNode;
  /**
   * "ready"  — a successful render exists and matches the current timeline.
   *            The Ready step turns emerald.
   * "stale"  — a render exists but the timeline has changed since.
   *            The Ready step turns amber with a tooltip nudging re-render.
   * "none"   — no successful render yet; default styling applies.
   */
  renderState?: "ready" | "stale" | "none";
}

export function PhaseHeader({ currentPhase, castName, onPhaseClick, actions, renderState = "none" }: PhaseHeaderProps) {
  const currentIdx = PHASES.findIndex((p) => p.key === currentPhase);

  return (
    // min-w-0 on the row + min-w-0 on each flex child is what lets the phase
    // row shrink instead of pushing the actions off-screen. title={castName}
    // gives the user the full name on hover when it's been truncated.
    <div className="flex items-center gap-4 px-4 py-3 border-b border-white/10 bg-black/30 backdrop-blur-xs min-w-0">
      {castName && (
        <span
          className="text-sm font-medium text-white/70 truncate shrink-0 max-w-[260px]"
          title={castName}
        >
          {castName}
        </span>
      )}
      <div className="flex items-center gap-1 flex-1 min-w-0 overflow-hidden">
        {PHASES.map((phase, idx) => {
          const isComplete = idx < currentIdx;
          const isCurrent = idx === currentIdx;
          const isClickable = isComplete && onPhaseClick;
          // FIX 9 — Ready step reacts to render state across all phases. A
          // successful render before the user reaches the Ready step is
          // still meaningful (they might be back in the editor making
          // tweaks) — green = "render is current", amber = "render exists
          // but is now stale because of edits".
          const isReadyStep = phase.key === "ready";
          const renderReady = isReadyStep && renderState === "ready";
          const renderStale = isReadyStep && renderState === "stale";
          return (
            <div key={phase.key} className="flex items-center gap-1">
              {idx > 0 && (
                <div
                  className={cn(
                    "w-4 md:w-6 h-px shrink-0",
                    renderReady && idx === PHASES.length - 1 ? "bg-emerald-500" :
                    renderStale && idx === PHASES.length - 1 ? "bg-amber-400" :
                    isComplete ? "bg-green-500" : "bg-white/20"
                  )}
                />
              )}
              <button
                disabled={!isClickable && !renderReady && !renderStale}
                onClick={() => isClickable && onPhaseClick(phase.key)}
                title={
                  renderStale
                    ? "Cast was edited after the last render. Re-render to get fresh output."
                    : renderReady
                      ? "Render is up to date with your latest edits."
                      : undefined
                }
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-colors",
                  renderReady && "bg-emerald-500/20 text-emerald-400",
                  renderStale && "bg-amber-500/15 text-amber-400",
                  !renderReady && !renderStale && isComplete && "bg-green-500/20 text-green-400",
                  !renderReady && !renderStale && isCurrent && "bg-accent/20 text-accent",
                  !renderReady && !renderStale && !isComplete && !isCurrent && "bg-white/5 text-white/40",
                  isClickable && "cursor-pointer hover:bg-green-500/30"
                )}
              >
                {renderReady ? (
                  <CheckCircle2 className="w-3 h-3" />
                ) : renderStale ? (
                  <AlertTriangle className="w-3 h-3" />
                ) : isComplete ? (
                  <CheckCircle2 className="w-3 h-3" />
                ) : isCurrent ? (
                  <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                ) : null}
                {phase.label}
              </button>
            </div>
          );
        })}
      </div>
      {actions && <div className="flex items-center gap-1.5 shrink-0 ml-auto">{actions}</div>}
    </div>
  );
}
