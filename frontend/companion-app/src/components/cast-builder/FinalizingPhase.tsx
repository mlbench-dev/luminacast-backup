/**
 * FinalizingPhase — Render progress UI with polling and completion/error states.
 *
 * Phase 3.1: Shows progress bar, per-block baking status, current step label,
 * estimated time, cancel button. On completion: video player + download.
 * On error: retry button + friendly error message.
 */
import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { Loader2, Film, CheckCircle2, XCircle, RefreshCw, Play, Download, ArrowLeft, Ban } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { castsApi } from "@/lib/api";
import { cdnUrl } from "@/lib/cdn";
import { downloadFile } from "@/lib/downloadFile";
import type { Cast } from "@/lib/types";

interface FinalizingPhaseProps {
  castId: string;
  cast?: Cast;
  outputFormat?: string;
  onReady: (cast: Cast) => void;
  onError: (error: string) => void;
}

type RenderState = "polling" | "completed" | "failed";

interface RenderInfo {
  renderId?: string;
  status?: string;
  bakingTotal?: number;
  bakingCompleted?: number;
  errorMessage?: string;
  outputVideoKey?: string;
}

const POLL_INTERVAL_MS = 5000;

function getStepLabel(status?: string, bakingCompleted?: number, bakingTotal?: number): string {
  switch (status) {
    case "queued":
      return "Queued — waiting for render slot...";
    case "baking":
      if (bakingTotal && bakingCompleted != null) {
        return `Baking block ${bakingCompleted + 1} of ${bakingTotal}...`;
      }
      return "Baking video blocks...";
    case "composing":
      return "Composing final video...";
    case "ready":
      return "Complete!";
    case "failed":
      return "Render failed";
    default:
      return "Rendering video...";
  }
}

function getProgressPercent(status?: string, bakingCompleted?: number, bakingTotal?: number): number {
  switch (status) {
    case "queued":
      return 5;
    case "baking":
      if (bakingTotal && bakingTotal > 0 && bakingCompleted != null) {
        // Baking is 0-80% of total progress
        return 5 + (bakingCompleted / bakingTotal) * 75;
      }
      return 10;
    case "composing":
      return 85;
    case "ready":
      return 100;
    case "failed":
      return 0;
    default:
      return 0;
  }
}

export function FinalizingPhase({ castId, cast, outputFormat, onReady, onError }: FinalizingPhaseProps) {
  const [renderState, setRenderState] = useState<RenderState>("polling");
  const [renderInfo, setRenderInfo] = useState<RenderInfo>({});
  const [progress, setProgress] = useState(0);
  const [step, setStep] = useState("Starting render...");
  const [elapsedSec, setElapsedSec] = useState(0);
  const [completedCast, setCompletedCast] = useState<Cast | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const startTimeRef = useRef(Date.now());

  const effectiveFormat = outputFormat || cast?.output_format || completedCast?.output_format;
  const defaultAspect = useMemo(() => {
    switch (effectiveFormat) {
      case "16:9":
        return 16 / 9;
      case "1:1":
        return 1;
      case "4:5":
        return 4 / 5;
      default:
        return 9 / 16;
    }
  }, [effectiveFormat]);

  const [videoAspect, setVideoAspect] = useState<number | null>(null);
  const activeAspect = videoAspect ?? defaultAspect;

  // Elapsed time counter
  useEffect(() => {
    if (renderState !== "polling") return;
    const timer = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [renderState]);

  // Polling loop
  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        // First try to get render-specific status from renders list
        const renders = await castsApi.listRenders(castId);
        const latestRender = renders?.[0];

        if (latestRender && !cancelled) {
          setRenderInfo({
            renderId: latestRender.id,
            status: latestRender.status,
            bakingTotal: latestRender.baking_chunks_total,
            bakingCompleted: latestRender.baking_chunks_completed,
            errorMessage: latestRender.error_message,
            outputVideoKey: latestRender.output_video_r2_key,
          });

          const newProgress = getProgressPercent(
            latestRender.status,
            latestRender.baking_chunks_completed,
            latestRender.baking_chunks_total,
          );
          setProgress((prev) => Math.max(prev, newProgress));
          setStep(getStepLabel(
            latestRender.status,
            latestRender.baking_chunks_completed,
            latestRender.baking_chunks_total,
          ));

          // Completion
          if (latestRender.status === "ready") {
            const cast = await castsApi.get(castId);
            setCompletedCast(cast);
            if (latestRender.output_video_r2_key) {
              setVideoUrl(cdnUrl(latestRender.output_video_r2_key));
            } else {
              // Fallback: try to find video from cast variants
              for (const block of cast.blocks || []) {
                for (const variant of block.variants || []) {
                  if (variant.final_video_key) {
                    setVideoUrl(cdnUrl(variant.final_video_key));
                    break;
                  }
                  if (variant.stream_url) {
                    setVideoUrl(variant.stream_url);
                    break;
                  }
                }
                if (videoUrl) break;
              }
            }
            setRenderState("completed");
            setProgress(100);
            return; // stop polling
          }

          // Failure
          if (latestRender.status === "failed") {
            setRenderState("failed");
            return;
          }
        } else {
          // Fallback: poll cast status directly
          const cast = await castsApi.get(castId);
          if (cancelled) return;

          const p = cast.generation_progress ?? 0;
          setProgress((prev) => Math.max(prev, p));
          setStep(cast.progress_step || "Rendering video...");

          if (cast.status?.toLowerCase() === "ready") {
            setCompletedCast(cast);
            onReady(cast);
            return;
          }
          if (cast.status?.toLowerCase() === "generation_failed") {
            setRenderState("failed");
            setRenderInfo((prev) => ({
              ...prev,
              errorMessage: cast.generation_error || "Video rendering failed",
            }));
            return;
          }
        }
      } catch (err: any) {
        if (!cancelled) console.error("Poll error:", err);
      }

      if (!cancelled) {
        setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    poll();
    return () => { cancelled = true; };
  }, [castId]);

  const handleRetry = useCallback(async () => {
    setRetrying(true);
    try {
      await castsApi.finalize(castId);
      setRenderState("polling");
      setProgress(0);
      setStep("Starting render...");
      startTimeRef.current = Date.now();
      setElapsedSec(0);
      setRenderInfo({});
    } catch (err: any) {
      onError(err?.response?.data?.detail || err.message || "Retry failed");
    } finally {
      setRetrying(false);
    }
  }, [castId, onError]);

  const handleBackToEditor = useCallback(() => {
    // Navigate back by calling onReady with completed cast or onError to reset
    if (completedCast) {
      onReady(completedCast);
    }
  }, [completedCast, onReady]);

  const handleDownload = useCallback(() => {
    if (videoUrl) {
      downloadFile(videoUrl, "cast-render.mp4");
    }
  }, [videoUrl]);

  const handlePlay = useCallback(() => {
    if (videoRef.current) {
      videoRef.current.paused ? videoRef.current.play() : videoRef.current.pause();
    }
  }, []);

  const formatElapsed = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  // ── Completed state ──
  if (renderState === "completed") {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-green-500/20 flex items-center justify-center">
            <CheckCircle2 className="w-5 h-5 text-green-400" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-white">Render Complete!</h2>
            <p className="text-sm text-white/50">
              Finished in {formatElapsed(elapsedSec)}
            </p>
          </div>
        </div>

        {/* Video Player */}
        <div
          className="rounded-xl overflow-hidden bg-black border border-white/10 relative group mx-auto w-full"
          style={{
            aspectRatio: String(activeAspect),
            maxHeight: "70vh",
            maxWidth: activeAspect < 1 ? `calc(70vh * ${activeAspect})` : undefined,
          }}
        >
          {videoUrl ? (
            <>
              <video
                ref={videoRef}
                src={videoUrl}
                controls
                autoPlay={false}
                playsInline
                className="w-full h-full object-contain bg-black"
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onLoadedMetadata={(e) => {
                  const { videoWidth, videoHeight } = e.currentTarget;
                  if (videoWidth > 0 && videoHeight > 0) {
                    setVideoAspect(videoWidth / videoHeight);
                  }
                }}
                data-testid="render-video-player"
              />
              {!playing && (
                <button
                  onClick={handlePlay}
                  className="absolute inset-0 flex items-center justify-center bg-black/30 transition-opacity group-hover:bg-black/40"
                >
                  <div className="w-16 h-16 rounded-full bg-white/20 backdrop-blur-xs flex items-center justify-center">
                    <Play className="w-8 h-8 text-white ml-1" />
                  </div>
                </button>
              )}
            </>
          ) : (
            <div className="w-full h-full min-h-[200px] flex items-center justify-center text-white/40">
              <div className="text-center space-y-2">
                <CheckCircle2 className="w-8 h-8 mx-auto text-green-400" />
                <p className="text-sm">Render complete — video processing</p>
              </div>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3 justify-center">
          {completedCast && (
            <Button variant="outline" onClick={handleBackToEditor} className="border-white/20">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to editor
            </Button>
          )}
          {videoUrl && (
            <Button variant="outline" onClick={handleDownload} className="border-white/20">
              <Download className="w-4 h-4 mr-2" />
              Download MP4
            </Button>
          )}
        </div>
      </div>
    );
  }

  // ── Failed state ──
  if (renderState === "failed") {
    return (
      <div className="max-w-lg mx-auto p-6 flex flex-col items-center justify-center min-h-[400px] space-y-6">
        <div className="w-16 h-16 rounded-full bg-red-500/20 flex items-center justify-center">
          <XCircle className="w-8 h-8 text-red-400" />
        </div>
        <div className="text-center space-y-2">
          <h2 className="text-lg font-semibold text-white">Render Failed</h2>
          <p className="text-sm text-white/50 max-w-md">
            {renderInfo.errorMessage || "Something went wrong during rendering. You can retry or go back to the editor."}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={handleRetry} disabled={retrying} className="bg-accent hover:bg-accent-hover text-white">
            {retrying ? (
              <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Retrying...</>
            ) : (
              <><RefreshCw className="w-4 h-4 mr-2" /> Retry Render</>
            )}
          </Button>
        </div>
      </div>
    );
  }

  // ── Polling / In-progress state ──
  return (
    <div className="max-w-lg mx-auto p-6 flex flex-col items-center justify-center min-h-[400px] space-y-6">
      <div className="w-16 h-16 rounded-full bg-purple-500/20 flex items-center justify-center">
        <Film className="w-8 h-8 text-purple-400 animate-pulse" />
      </div>
      <div className="text-center space-y-2">
        <h2 className="text-lg font-semibold text-white">Rendering Video</h2>
        <p className="text-sm text-white/50">{step}</p>
      </div>

      {/* Progress bar */}
      <div className="w-full space-y-2">
        <Progress value={progress} className="h-2" />
        <div className="flex items-center justify-between text-xs text-white/40">
          <span>{Math.round(progress)}%</span>
          <span>Elapsed: {formatElapsed(elapsedSec)}</span>
        </div>
      </div>

      {/* Per-block baking status */}
      {renderInfo.bakingTotal && renderInfo.bakingTotal > 0 && (
        <div className="w-full space-y-1">
          <p className="text-[11px] text-white/30 uppercase tracking-wider">Block Progress</p>
          <div className="flex gap-1">
            {Array.from({ length: renderInfo.bakingTotal }, (_, i) => {
              const completed = (renderInfo.bakingCompleted ?? 0) > i;
              const current = (renderInfo.bakingCompleted ?? 0) === i && renderInfo.status === "baking";
              return (
                <div
                  key={i}
                  className={`flex-1 h-2 rounded-full transition-colors ${
                    completed
                      ? "bg-green-500"
                      : current
                        ? "bg-purple-500 animate-pulse"
                        : "bg-white/10"
                  }`}
                  title={`Block ${i + 1}: ${completed ? "done" : current ? "baking..." : "pending"}`}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* Status details */}
      <div className="flex items-center gap-2 text-white/40 text-xs">
        <Loader2 className="w-3 h-3 animate-spin" />
        {renderInfo.status === "queued"
          ? "Waiting for render slot..."
          : renderInfo.status === "composing"
            ? "Almost there — assembling final video..."
            : "Rendering may take several minutes"}
      </div>
    </div>
  );
}
