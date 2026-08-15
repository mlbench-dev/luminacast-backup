import { useState, useRef, useCallback, useEffect } from "react";
import { ChevronLeft, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface SegmentSelectorProps {
  videoUrl: string;
  duration: number;
  onBack: () => void;
  onConfirm: (start: number, end: number) => void;
  isPending?: boolean;
  videoLabel?: string;
}

const MIN_SEGMENT = 15;
const MAX_SEGMENT = 120;

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function SegmentSelector({
  videoUrl,
  duration,
  onBack,
  onConfirm,
  isPending = false,
  videoLabel,
}: SegmentSelectorProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(Math.min(duration, MAX_SEGMENT));

  // Clamp end when duration changes
  useEffect(() => {
    setEnd(Math.min(duration, MAX_SEGMENT));
  }, [duration]);

  const handleStartChange = useCallback(
    (val: number) => {
      const clamped = Math.max(0, Math.min(val, end - MIN_SEGMENT));
      // Also enforce max segment
      const newStart = end - clamped > MAX_SEGMENT ? end - MAX_SEGMENT : clamped;
      setStart(newStart);
      if (videoRef.current) videoRef.current.currentTime = newStart;
    },
    [end],
  );

  const handleEndChange = useCallback(
    (val: number) => {
      const clamped = Math.min(duration, Math.max(val, start + MIN_SEGMENT));
      // Enforce max segment
      const newEnd = clamped - start > MAX_SEGMENT ? start + MAX_SEGMENT : clamped;
      setEnd(newEnd);
      if (videoRef.current) videoRef.current.currentTime = newEnd;
    },
    [duration, start],
  );

  const selectedDuration = Math.round(end - start);

  return (
    <div className="space-y-4" data-testid="segment-selector">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs text-text-muted hover:text-text transition"
          data-testid="segment-back"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          Back to videos
        </button>
        {videoLabel && (
          <span className="text-xs text-text-muted">{videoLabel}</span>
        )}
      </div>

      {/* Video player */}
      {/* Video player — native controls only, no custom overlay (B-034) */}
      <div
        className="relative mx-auto overflow-hidden rounded-lg border border-border bg-black"
        style={{ maxWidth: 400 }}
      >
        <video
          ref={videoRef}
          src={videoUrl}
          className="w-full"
          style={{ aspectRatio: "9/16", objectFit: "contain" }}
          controls
          playsInline
          preload="auto"
          /* crossOrigin removed — R2 doesn't return CORS headers, causes video load failures (B-083) */
        />
      </div>

      {/* Dual-handle range slider */}
      <div className="mx-auto space-y-2" style={{ maxWidth: 400 }}>
        <div className="relative h-8">
          {/* Track background */}
          <div className="absolute top-1/2 left-0 right-0 h-1.5 -translate-y-1/2 rounded-full bg-border" />
          {/* Highlighted range */}
          <div
            className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-accent"
            style={{
              left: `${(start / duration) * 100}%`,
              width: `${((end - start) / duration) * 100}%`,
            }}
          />
          {/* Start handle */}
          <input
            type="range"
            min={0}
            max={Math.round(duration)}
            step={0.5}
            value={start}
            onChange={(e) => handleStartChange(Number(e.target.value))}
            className="absolute inset-0 w-full appearance-none bg-transparent pointer-events-none [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:cursor-grab [&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:appearance-none [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-accent [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-white [&::-moz-range-thumb]:shadow-md [&::-moz-range-thumb]:cursor-grab"
            style={{ zIndex: 3 }}
            data-testid="segment-start-handle"
          />
          {/* End handle */}
          <input
            type="range"
            min={0}
            max={Math.round(duration)}
            step={0.5}
            value={end}
            onChange={(e) => handleEndChange(Number(e.target.value))}
            className="absolute inset-0 w-full appearance-none bg-transparent pointer-events-none [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:cursor-grab [&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:appearance-none [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-accent [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-white [&::-moz-range-thumb]:shadow-md [&::-moz-range-thumb]:cursor-grab"
            style={{ zIndex: 4 }}
            data-testid="segment-end-handle"
          />
        </div>

        {/* Timestamps */}
        <div className="flex items-center justify-between text-xs text-text-dim">
          <span>{formatTime(start)}</span>
          <span className="font-medium text-accent">Selected: {selectedDuration}s</span>
          <span>{formatTime(end)}</span>
        </div>

        <p className="text-[10px] text-text-muted text-center">
          Pick a segment where the creator talks clearly, alone, facing the camera.
        </p>
      </div>

      {/* Confirm button */}
      <Button
        onClick={() => onConfirm(start, end)}
        disabled={isPending || selectedDuration < MIN_SEGMENT}
        className="w-full max-w-[400px] mx-auto block"
        data-testid="use-segment-button"
      >
        {isPending ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Processing...
          </>
        ) : (
          `Use This Segment (${selectedDuration}s) →`
        )}
      </Button>
      {selectedDuration < MIN_SEGMENT && (
        <p className="text-xs text-center text-danger" data-testid="segment-too-short">
          Minimum 15 seconds required
        </p>
      )}
    </div>
  );
}
