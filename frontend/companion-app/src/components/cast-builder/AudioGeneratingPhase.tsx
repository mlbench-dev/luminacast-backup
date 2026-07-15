import { useEffect, useRef, useState } from "react";
import { Loader2, Volume2 } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { castsApi } from "@/lib/api";
import type { Cast } from "@/lib/types";

interface AudioGeneratingPhaseProps {
  castId: string;
  onReady: (cast: Cast) => void;
  onError: (error: string) => void;
}

// Backend reports generation_progress as a 0-1 fraction. Some legacy paths
// emit 0-100 — accept both and normalize so the bar can't stick at 1%.
function normaliseProgress(raw: number | null | undefined): number {
  if (raw == null || Number.isNaN(raw)) return 0;
  if (raw <= 1) return Math.max(0, Math.min(1, raw)) * 100;
  return Math.max(0, Math.min(100, raw));
}

export function AudioGeneratingPhase({ castId, onReady, onError }: AudioGeneratingPhaseProps) {
  const [progress, setProgress] = useState(0);
  const [step, setStep] = useState("Generating audio...");
  // Use a ref so the polling closure can read the latest progress without
  // re-binding, and so we never let the bar regress (rare race when a new
  // stage's first tick reports a slightly lower aggregate than the prior).
  const progressRef = useRef(0);
  const stuckCounterRef = useRef(0);
  const lastProgressRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const cast = await castsApi.get(castId);
        if (cancelled) return;

        const incoming = normaliseProgress(cast.generation_progress);
        // Monotonic guard — never let the bar move backwards.
        const next = Math.max(progressRef.current, incoming);
        progressRef.current = next;
        setProgress(next);
        setStep(cast.progress_step || "Generating audio...");

        // Stuck detection: if progress hasn't moved across 15 polls (≈30s
        // at 2s cadence) but the job is still running, breadcrumb it so
        // we can spot pipeline hangs in production traces.
        if (next === lastProgressRef.current && next > 0 && next < 99) {
          stuckCounterRef.current += 1;
          if (stuckCounterRef.current === 15) {
            try {
              // Sentry browser SDK is loaded globally elsewhere; guard so
              // a missing window.Sentry never breaks the polling loop.
              const w = window as unknown as { Sentry?: { addBreadcrumb?: (b: object) => void } };
              w.Sentry?.addBreadcrumb?.({
                category: "audio-progress",
                message: `Audio progress stuck at ${next}% for ~30s`,
                level: "warning",
                data: { castId, step: cast.progress_step },
              });
            } catch {
              // No-op — telemetry must never break UX.
            }
          }
        } else {
          stuckCounterRef.current = 0;
          lastProgressRef.current = next;
        }

        const status = cast.status?.toLowerCase();
        if (status === "tts_ready" || status === "generating_videos" || status === "ready") {
          onReady(cast);
          return;
        }
        if (status === "generation_failed") {
          onError(cast.generation_error || "Audio generation failed");
          return;
        }
      } catch (err: any) {
        if (!cancelled) {
          console.error("Poll error:", err);
        }
      }
      if (!cancelled) {
        // 2s — fast enough that the bar moves visibly when each block
        // completes, slow enough that we don't hammer the DB.
        timer = setTimeout(poll, 2000);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [castId]);

  return (
    <div className="max-w-lg mx-auto p-6 flex flex-col items-center justify-center min-h-[400px] space-y-6">
      <div className="w-16 h-16 rounded-full bg-accent/20 flex items-center justify-center">
        <Volume2 className="w-8 h-8 text-accent animate-pulse" />
      </div>
      <div className="text-center space-y-2">
        <h2 className="text-lg font-semibold text-white">Generating Audio</h2>
        <p className="text-sm text-white/50">{step}</p>
      </div>
      <div className="w-full space-y-2">
        <Progress value={progress} className="h-2" />
        <p className="text-xs text-white/40 text-center">{Math.round(progress)}%</p>
      </div>
      <div className="flex items-center gap-2 text-white/40 text-xs">
        <Loader2 className="w-3 h-3 animate-spin" />
        This may take a few minutes
      </div>
    </div>
  );
}
