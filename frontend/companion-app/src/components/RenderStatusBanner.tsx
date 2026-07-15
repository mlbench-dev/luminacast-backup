import { AlertTriangle, Clock, Loader2, XCircle, RefreshCw } from "lucide-react";

interface RenderStatusBannerProps {
  state: string;
  position?: number;
  etaSeconds?: number | null;
  confidence?: "high" | "low" | "none";
  errorMessage?: string | null;
  elapsedSeconds?: number | null;
  onRetry?: () => void;
  onContactSupport?: () => void;
}

function formatMinutes(seconds: number): string {
  const mins = Math.round(seconds / 60);
  if (mins < 1) return "less than a minute";
  if (mins === 1) return "~1 minute";
  return `~${mins} minutes`;
}

export function RenderStatusBanner({
  state,
  position = 0,
  etaSeconds,
  confidence = "none",
  errorMessage,
  elapsedSeconds,
  onRetry,
  onContactSupport,
}: RenderStatusBannerProps) {
  if (state === "COMPLETED") return null;

  const remaining =
    etaSeconds != null && elapsedSeconds != null
      ? Math.max(0, etaSeconds - elapsedSeconds)
      : null;

  // QUEUED
  if (state === "QUEUED") {
    let message: string;
    if (confidence === "high" && etaSeconds != null) {
      message = `Rush hour — you're #${position} in queue. Estimated wait: ${formatMinutes(etaSeconds)}.`;
    } else if (confidence === "low" && etaSeconds != null) {
      const low = Math.round(etaSeconds * 0.7 / 60);
      const high = Math.round(etaSeconds * 1.5 / 60);
      message = `In queue — you're #${position}. Estimated wait: roughly ${low}-${high} minutes.`;
    } else {
      message = `In queue — you're #${position}. We don't have enough history yet to estimate wait time accurately.`;
    }
    return (
      <div className="flex items-center gap-3 rounded-lg bg-purple-900/40 border border-purple-500/30 px-4 py-3 text-sm text-purple-200">
        <Clock className="h-4 w-4 shrink-0 text-purple-400" />
        <span>{message}</span>
      </div>
    );
  }

  // IN_PROGRESS
  if (state === "IN_PROGRESS") {
    const progressMsg =
      remaining != null && remaining > 0
        ? `Cooking your video — about ${formatMinutes(remaining)} left.`
        : "Wrapping up…";
    const pct =
      remaining != null && etaSeconds != null && etaSeconds > 0
        ? Math.min(100, Math.round(((etaSeconds - remaining) / etaSeconds) * 100))
        : null;
    return (
      <div className="rounded-lg bg-purple-900/40 border border-purple-500/30 px-4 py-3 text-sm text-purple-200">
        <div className="flex items-center gap-3">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-purple-400" />
          <span>{progressMsg}</span>
        </div>
        {pct != null && (
          <div className="mt-2 h-1.5 w-full rounded-full bg-purple-900/60">
            <div
              className="h-full rounded-full bg-purple-500 transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
      </div>
    );
  }

  // STALLED
  if (state === "STALLED") {
    return (
      <div className="flex items-center gap-3 rounded-lg bg-amber-900/40 border border-amber-500/30 px-4 py-3 text-sm text-amber-200">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
        <span>
          Hm, this is taking longer than usual. We're checking — if this persists
          we'll retry automatically.
        </span>
      </div>
    );
  }

  // FAILED
  if (state === "FAILED") {
    return (
      <div className="rounded-lg bg-red-900/40 border border-red-500/30 px-4 py-3 text-sm text-red-200">
        <div className="flex items-center gap-3">
          <XCircle className="h-4 w-4 shrink-0 text-red-400" />
          <span>Render failed: {errorMessage || "Unknown error"}</span>
        </div>
        <div className="mt-2 flex gap-2">
          {onRetry && (
            <button
              onClick={onRetry}
              className="inline-flex items-center gap-1.5 rounded-md bg-red-800/60 px-3 py-1.5 text-xs font-medium text-red-100 hover:bg-red-700/60 transition-colors"
            >
              <RefreshCw className="h-3 w-3" />
              Retry
            </button>
          )}
          {onContactSupport && (
            <button
              onClick={onContactSupport}
              className="inline-flex items-center gap-1.5 rounded-md bg-red-800/40 px-3 py-1.5 text-xs font-medium text-red-200 hover:bg-red-700/40 transition-colors"
            >
              Contact support
            </button>
          )}
        </div>
      </div>
    );
  }

  // ENDPOINT_DOWN
  if (state === "ENDPOINT_DOWN") {
    return (
      <div className="flex items-center gap-3 rounded-lg bg-amber-900/40 border border-amber-500/30 px-4 py-3 text-sm text-amber-200">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
        <span>
          Our render service is temporarily unavailable. We'll retry automatically
          when it's back.
        </span>
      </div>
    );
  }

  return null;
}
