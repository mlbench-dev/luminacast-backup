import { useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin from "wavesurfer.js/dist/plugins/regions";

interface OverlayTiming {
  id: string;
  start_seconds: number;
  end_seconds: number;
  text?: string;
  kind: string;
}

export function WaveformOverlayEditor({
  audioUrl,
  duration,
  overlays,
  onOverlayTimingChange,
}: {
  audioUrl: string;
  duration: number;
  overlays: OverlayTiming[];
  onOverlayTimingChange: (overlayId: string, start: number, end: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wavesurferRef = useRef<WaveSurfer | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);

  useEffect(() => {
    if (!containerRef.current || !audioUrl) return;
    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: "#555",
      progressColor: "#a855f7",
      cursorColor: "#fff",
      height: 80,
      plugins: [regions],
      url: audioUrl,
    });
    wavesurferRef.current = ws;

    ws.on("ready", () => {
      overlays.forEach((ov) => {
        regions.addRegion({
          id: ov.id,
          start: ov.start_seconds,
          end: ov.end_seconds,
          color:
            ov.kind === "text"
              ? "rgba(168,85,247,0.3)"
              : "rgba(34,197,94,0.3)",
          content: ov.text || ov.kind,
          drag: true,
          resize: true,
        });
      });
    });

    regions.on("region-updated", (region: any) => {
      onOverlayTimingChange(region.id as string, region.start, region.end);
    });

    ws.on("timeupdate", (t: number) => setCurrentTime(t));
    ws.on("play", () => setPlaying(true));
    ws.on("pause", () => setPlaying(false));

    return () => ws.destroy();
  }, [audioUrl]);

  const togglePlay = () => wavesurferRef.current?.playPause();

  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      <div className="flex items-center justify-between mb-2">
        <button
          onClick={togglePlay}
          className="text-sm px-2 py-1 rounded bg-card hover:bg-accent/20 text-text"
        >
          {playing ? "\u23F8 Pause" : "\u25B6 Play"}
        </button>
        <span className="text-xs text-text-muted">
          {currentTime.toFixed(1)}s / {duration.toFixed(1)}s
        </span>
      </div>
      <div ref={containerRef} />
      <p className="text-xs text-text-muted mt-2">
        Drag region edges to retime overlays.
      </p>
    </div>
  );
}
