/**
 * RenderPlayer — Production-grade video player for render playback.
 *
 * Features: centered pop-out matching the video's own aspect ratio, custom controls (play/pause, seek, volume,
 * fullscreen, PiP), keyboard shortcuts, version/quality badges, download button.
 * Portaled to document.body for proper z-index.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { createPortal } from "react-dom";
import { Play, Pause, Volume2, VolumeX, Maximize2, Minimize2, Download, X, RotateCcw } from "lucide-react";
import { cdnUrl } from "@/lib/cdn";
import { downloadFile } from "@/lib/downloadFile";

interface RenderPlayerProps {
  renderId: string;
  videoKey: string;
  version?: number;
  quality?: string;
  outputFormat?: string;
  onClose: () => void;
}

export function RenderPlayer({ renderId, videoKey, version, quality, outputFormat, onClose }: RenderPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [pipSupported] = useState(() => typeof document !== "undefined" && "pictureInPictureEnabled" in document);

  const defaultAspect = (() => {
    switch (outputFormat) {
      case "16:9":
        return 16 / 9;
      case "1:1":
        return 1;
      case "4:5":
        return 4 / 5;
      default:
        return 9 / 16;
    }
  })();
  const [videoAspect, setVideoAspect] = useState<number | null>(null);

  // Keyboard shortcuts
  useEffect(() => {
    const HANDLED = new Set([
      " ", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
      "f", "F", "m", "M", "Escape",
    ]);
    const handler = (e: KeyboardEvent) => {
      const v = videoRef.current;
      if (!v) return;
      // Ignore when typing in an input
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (!HANDLED.has(e.key)) return;

      // This modal is portaled OVER the editor, whose Remotion preview player
      // has its own window-level Space = play/pause shortcut (and other
      // editor shortcuts). Swallow the event here — capture phase + stop —
      // so watching the finished render never also drives the editor player.
      e.preventDefault();
      e.stopImmediatePropagation();

      switch (e.key) {
        case " ":
          v.paused ? v.play() : v.pause();
          break;
        case "ArrowLeft":
          v.currentTime = Math.max(0, v.currentTime - 5);
          break;
        case "ArrowRight":
          v.currentTime = Math.min(v.duration, v.currentTime + 5);
          break;
        case "ArrowUp":
          v.volume = Math.min(1, v.volume + 0.1);
          setVolume(v.volume);
          break;
        case "ArrowDown":
          v.volume = Math.max(0, v.volume - 0.1);
          setVolume(v.volume);
          break;
        case "f":
        case "F":
          toggleFullscreen();
          break;
        case "m":
        case "M":
          v.muted = !v.muted;
          setMuted(v.muted);
          break;
        case "Escape":
          if (!document.fullscreenElement) onClose();
          break;
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [onClose]);

  // Fullscreen change listener
  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  const toggleFullscreen = useCallback(async () => {
    if (!containerRef.current) return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await containerRef.current.requestFullscreen();
      }
    } catch {
      // Fullscreen not supported or denied
    }
  }, []);

  const togglePip = useCallback(async () => {
    const v = videoRef.current;
    if (!v || !pipSupported) return;
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
      } else {
        await v.requestPictureInPicture();
      }
    } catch {
      // PiP not supported or denied
    }
  }, [pipSupported]);

  const handleSeek = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const v = videoRef.current;
    if (!v) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    v.currentTime = pct * v.duration;
  }, []);

  const fmt = (s: number) => {
    if (!isFinite(s)) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const qualityLabel = quality === "hd_plus" ? "HD+" : quality === "hd" ? "HD" : "Simple";

  return createPortal(
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/95 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        ref={containerRef}
        className="relative bg-black rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        style={(() => {
          const ar = videoAspect ?? defaultAspect;
          return {
            width: `min(90vw, calc(90vh * ${ar}))`,
            maxWidth: "90vw",
            maxHeight: "90vh",
            aspectRatio: String(ar),
          };
        })()}
      >
        {/* Header overlay */}
        <div className="absolute top-0 left-0 right-0 p-3 bg-gradient-to-b from-black/80 to-transparent flex items-center justify-between z-10">
          <div className="flex items-center gap-2">
            {version != null && <span className="text-xs text-white/80">v{version}</span>}
            {quality && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                quality === "hd_plus" ? "bg-purple-500/20 text-purple-300 border-purple-500/30" :
                quality === "hd" ? "bg-blue-500/20 text-blue-300 border-blue-500/30" :
                "bg-white/10 text-white/70 border-white/10"
              }`}>{qualityLabel}</span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={(e) => {
                e.stopPropagation();
                downloadFile(cdnUrl(videoKey), `render-v${version || 1}-${qualityLabel}.mp4`);
              }}
              className="p-1.5 rounded hover:bg-white/10 text-white/70 hover:text-white transition-colors"
              title="Download"
            >
              <Download className="w-4 h-4" />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-white/10 text-white/70 hover:text-white transition-colors"
              title="Close (Esc)"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <video
          ref={videoRef}
          src={cdnUrl(videoKey)}
          autoPlay
          playsInline
          className="w-full h-full object-contain bg-black"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => {
            setDuration(e.currentTarget.duration);
            setVolume(e.currentTarget.volume);
            const { videoWidth, videoHeight } = e.currentTarget;
            if (videoWidth > 0 && videoHeight > 0) setVideoAspect(videoWidth / videoHeight);
          }}
          onProgress={(e) => {
            const v = e.currentTarget;
            if (v.buffered.length > 0) setBuffered(v.buffered.end(v.buffered.length - 1));
          }}
          onClick={() => {
            const v = videoRef.current;
            if (!v) return;
            v.paused ? v.play() : v.pause();
          }}
        />

        {/* Footer controls */}
        <div className="absolute bottom-0 left-0 right-0 p-3 bg-gradient-to-t from-black/80 to-transparent">
          {/* Seek bar */}
          <div
            className="h-1 bg-white/20 rounded cursor-pointer mb-2 group relative"
            onClick={handleSeek}
          >
            <div
              className="absolute top-0 left-0 h-full bg-white/30 rounded"
              style={{ width: `${(buffered / duration) * 100 || 0}%` }}
            />
            <div
              className="absolute top-0 left-0 h-full bg-accent rounded"
              style={{ width: `${(currentTime / duration) * 100 || 0}%` }}
            />
          </div>
          {/* Controls row */}
          <div className="flex items-center gap-2 text-white/80">
            <button onClick={() => {
              const v = videoRef.current;
              if (!v) return;
              v.paused ? v.play() : v.pause();
            }} title={playing ? "Pause (Space)" : "Play (Space)"}>
              {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </button>
            <button onClick={() => {
              const v = videoRef.current;
              if (!v) return;
              v.currentTime = 0;
            }} title="Restart">
              <RotateCcw className="w-4 h-4" />
            </button>
            <span className="text-[11px] tabular-nums">{fmt(currentTime)} / {fmt(duration)}</span>
            <div className="flex-1" />
            <button onClick={() => {
              const v = videoRef.current;
              if (!v) return;
              v.muted = !v.muted;
              setMuted(v.muted);
            }} title={muted ? "Unmute (M)" : "Mute (M)"}>
              {muted || volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
            </button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={muted ? 0 : volume}
              onChange={(e) => {
                const v = videoRef.current;
                if (!v) return;
                v.volume = Number(e.target.value);
                v.muted = v.volume === 0;
                setVolume(v.volume);
                setMuted(v.muted);
              }}
              className="w-16 accent-white/60"
            />
            {pipSupported && (
              <button onClick={togglePip} title="Picture-in-Picture">
                {/* PiP icon — inline SVG since lucide doesn't have the exact one */}
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="4" width="20" height="16" rx="2" />
                  <rect x="12" y="12" width="8" height="6" rx="1" fill="currentColor" fillOpacity="0.3" />
                </svg>
              </button>
            )}
            <button onClick={toggleFullscreen} title={isFullscreen ? "Exit fullscreen (F)" : "Fullscreen (F)"}>
              {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
