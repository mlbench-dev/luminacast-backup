import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { avatarApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import {
  CheckCircle, Loader2, Circle, XCircle, AlertTriangle, RefreshCw, Clock,
} from "lucide-react";

interface PipelineStep {
  job_id: string | null;
  state: string;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  progress_percent: number | null;
  label: string;
  default_eta_seconds: number;
}

interface PipelineState {
  avatar_id: string;
  avatar_status: string;
  avatar_phase: string;
  steps: Record<string, PipelineStep>;
  overall: string;
  failed_step: string | null;
  running_step: string | null;
  pipeline_type: string;
}

interface PipelineProgressViewProps {
  avatarId: string;
  onComplete?: () => void;
}

const STATE_CONFIG: Record<string, {
  icon: typeof CheckCircle;
  color: string;
  bgColor: string;
  label: string;
}> = {
  COMPLETED: { icon: CheckCircle, color: "text-green-400", bgColor: "bg-green-500/20", label: "Done" },
  IN_PROGRESS: { icon: Loader2, color: "text-blue-400", bgColor: "bg-blue-500/20", label: "In progress" },
  QUEUED: { icon: Circle, color: "text-zinc-400", bgColor: "bg-zinc-500/10", label: "Queued" },
  PENDING: { icon: Circle, color: "text-zinc-600", bgColor: "bg-zinc-800/50", label: "Waiting" },
  FAILED: { icon: XCircle, color: "text-red-400", bgColor: "bg-red-500/20", label: "Failed" },
  STALLED: { icon: AlertTriangle, color: "text-amber-400", bgColor: "bg-amber-500/20", label: "Stalled" },
  ENDPOINT_DOWN: { icon: AlertTriangle, color: "text-amber-400", bgColor: "bg-amber-500/20", label: "Service issue" },
};

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

function getElapsedSeconds(startedAt: string | null): number {
  if (!startedAt) return 0;
  return Math.max(0, (Date.now() - new Date(startedAt).getTime()) / 1000);
}

export function PipelineProgressView({ avatarId, onComplete }: PipelineProgressViewProps) {
  const navigate = useNavigate();
  const [state, setState] = useState<PipelineState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  const fetchState = useCallback(async () => {
    try {
      const data = await avatarApi.getRenderJobs(avatarId);
      setState(data);
      setError(null);

      // If all complete, redirect
      if (data.overall === "complete" || data.avatar_status === "READY" || data.avatar_status === "APPROVED") {
        onComplete?.();
      }
    } catch (err: any) {
      // No render jobs yet — not an error, just no pipeline data
      if (err?.response?.status === 404) return;
      setError(err?.response?.data?.detail || "Failed to load pipeline status");
    }
  }, [avatarId, onComplete]);

  // Initial fetch + polling every 10s when non-terminal
  useEffect(() => {
    fetchState();
    const interval = setInterval(() => {
      if (state?.overall === "complete") return;
      fetchState();
    }, 10000);
    return () => clearInterval(interval);
  }, [fetchState, state?.overall]);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await avatarApi.resumePipeline(avatarId);
      await fetchState();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Retry failed");
    } finally {
      setRetrying(false);
    }
  };

  if (!state || Object.keys(state.steps).length === 0) {
    return null; // No pipeline data yet
  }

  const steps = Object.entries(state.steps);
  const totalEtaRemaining = steps.reduce((sum, [, step]) => {
    if (step.state === "COMPLETED") return sum;
    if (step.state === "IN_PROGRESS") {
      const elapsed = getElapsedSeconds(step.started_at);
      return sum + Math.max(0, step.default_eta_seconds - elapsed);
    }
    if (step.state === "PENDING" || step.state === "QUEUED") {
      return sum + step.default_eta_seconds;
    }
    return sum;
  }, 0);

  const completedCount = steps.filter(([, s]) => s.state === "COMPLETED").length;

  return (
    <div className="w-full max-w-lg mx-auto py-6 px-4">
      {/* Banner */}
      <div className="mb-6 p-4 rounded-lg bg-zinc-900/80 border border-white/10">
        <div className="flex items-center gap-2 mb-1">
          {state.overall === "failed" ? (
            <XCircle className="w-5 h-5 text-red-400" />
          ) : state.overall === "complete" ? (
            <CheckCircle className="w-5 h-5 text-green-400" />
          ) : (
            <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />
          )}
          <span className="text-white font-medium">
            {state.overall === "complete"
              ? "Avatar creation complete!"
              : state.overall === "failed"
              ? "Pipeline needs attention"
              : "Creating your Avatar"}
          </span>
        </div>
        {state.overall === "running" && totalEtaRemaining > 0 && (
          <p className="text-sm text-white/50 ml-7">
            About {formatDuration(totalEtaRemaining)} remaining ({completedCount}/{steps.length} steps done)
          </p>
        )}
      </div>

      {/* Step checklist */}
      <div className="space-y-1">
        {steps.map(([stepKey, step], idx) => {
          const config = STATE_CONFIG[step.state] || STATE_CONFIG.PENDING;
          const Icon = config.icon;
          const isAnimated = step.state === "IN_PROGRESS";
          const isFailed = step.state === "FAILED" || step.state === "STALLED";

          return (
            <div
              key={stepKey}
              className={cn(
                "flex items-start gap-3 p-3 rounded-lg transition-colors",
                config.bgColor,
              )}
            >
              {/* Step icon */}
              <div className="mt-0.5 shrink-0">
                <Icon
                  className={cn("w-5 h-5", config.color, isAnimated && "animate-spin")}
                />
              </div>

              {/* Step content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={cn("text-sm font-medium", config.color)}>
                    {step.label}
                  </span>
                  {step.state === "COMPLETED" && step.started_at && step.completed_at && (
                    <span className="text-[10px] text-white/30">
                      Done in {formatDuration(
                        (new Date(step.completed_at).getTime() - new Date(step.started_at).getTime()) / 1000
                      )}
                    </span>
                  )}
                </div>

                {/* Secondary line */}
                {step.state === "IN_PROGRESS" && (
                  <p className="text-xs text-white/40 mt-0.5">
                    {step.default_eta_seconds > 0 ? (
                      <>About {formatDuration(Math.max(0, step.default_eta_seconds - getElapsedSeconds(step.started_at)))} left</>
                    ) : (
                      "Processing..."
                    )}
                  </p>
                )}

                {isFailed && (
                  <div className="mt-1">
                    <p className="text-xs text-red-300/70">
                      {step.error_message || "Something went wrong"}
                    </p>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={handleRetry}
                      disabled={retrying}
                      className="mt-2 h-7 text-xs border-red-500/30 text-red-300 hover:bg-red-500/10"
                    >
                      {retrying ? (
                        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                      ) : (
                        <RefreshCw className="w-3 h-3 mr-1" />
                      )}
                      Retry this step
                    </Button>
                  </div>
                )}

                {step.state === "STALLED" && (
                  <p className="text-xs text-amber-300/70 mt-0.5">
                    Taking longer than usual, we're checking...
                  </p>
                )}

                {step.state === "QUEUED" && (
                  <p className="text-xs text-white/30 mt-0.5 flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    Waiting for previous step
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Error banner */}
      {error && (
        <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}
