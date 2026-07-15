/**
 * RendersCollection — Dropdown showing all renders for a cast.
 *
 * Portal-based dropdown to escape stacking contexts (Phase 1 fix).
 * Shows: status badge, quality, timestamp, duration per render
 * Star = selected for publishing, click render -> plays in RenderPlayer modal
 * Hover-group bridge with 400ms close delay.
 * Keyboard nav: Arrow keys cycle, Enter plays, Escape closes.
 */
import { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle } from "react";
import { createPortal } from "react-dom";
import { Film, Star, Play, Loader2, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { castsApi } from "@/lib/api";
import { cdnUrl } from "@/lib/cdn";
import { toast } from "@/hooks/useToast";
import { useAnchoredPortal } from "./hooks/useAnchoredPortal";
import { RenderPlayer } from "./RenderPlayer";

interface RenderItem {
  id: string;
  status: string;
  version?: number;
  quality?: string;
  duration_seconds?: number;
  is_selected: boolean;
  thumbnail_key?: string;
  output_video_r2_key?: string;
  error_message?: string;
  created_at?: string;
  completed_at?: string;
}

interface RendersCollectionProps {
  castId: string;
}

export interface RendersCollectionHandle {
  playLatestRender: () => void;
}

const STATUS_BADGE: Record<string, { label: string; color: string }> = {
  queued: { label: "Queued", color: "bg-gray-500/20 text-gray-300" },
  baking: { label: "Rendering", color: "bg-amber-500/20 text-amber-300" },
  composing: { label: "Composing", color: "bg-blue-500/20 text-blue-300" },
  ready: { label: "Ready", color: "bg-green-500/20 text-green-300" },
  failed: { label: "Failed", color: "bg-red-500/20 text-red-300" },
};

export const RendersCollection = forwardRef<RendersCollectionHandle, RendersCollectionProps>(
  function RendersCollection({ castId }, ref) {
  const triggerRef = useRef<HTMLDivElement>(null);
  const portalRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [renders, setRenders] = useState<RenderItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [playingRender, setPlayingRender] = useState<RenderItem | null>(null);
  const [justCompletedId, setJustCompletedId] = useState<string | null>(null);
  const [focusIdx, setFocusIdx] = useState<number>(-1);

  // Portal positioning
  const dropdownPos = useAnchoredPortal(triggerRef, open, { align: "right", minWidth: 360 });

  // Hover-group manager
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enter = () => {
    if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
  };
  const leave = () => {
    if (open) hoverTimerRef.current = setTimeout(() => setOpen(false), 400);
  };

  const fetchRenders = useCallback(async () => {
    if (!castId) return;
    setLoading(true);
    try {
      const data = await castsApi.listRenders(castId);
      const newRenders = Array.isArray(data) ? data : [];
      // Detect newly completed renders
      setRenders((prev) => {
        const prevIds = new Set(prev.filter(r => r.status === "ready").map(r => r.id));
        const newReady = newRenders.find((r: RenderItem) => r.status === "ready" && !prevIds.has(r.id));
        if (newReady) {
          setJustCompletedId(newReady.id);
          setTimeout(() => setJustCompletedId(null), 3000);
        }
        return newRenders;
      });
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [castId]);

  useEffect(() => {
    fetchRenders();
  }, [fetchRenders]);

  // Expose playLatestRender to parent via ref
  useImperativeHandle(ref, () => ({
    playLatestRender: async () => {
      if (!castId) return;
      try {
        const data = await castsApi.listRenders(castId);
        const list = Array.isArray(data) ? data : [];
        setRenders(list);
        const latestReady = list.find((r: RenderItem) => r.status === "ready" && r.output_video_r2_key);
        if (latestReady) {
          setPlayingRender(latestReady);
        } else {
          setOpen(true);
        }
      } catch {
        setOpen(true);
      }
    },
  }), [castId]);

  // Click-outside close — check both trigger AND portal
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      if (portalRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Escape close + keyboard nav
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); return; }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(renders.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter" && focusIdx >= 0 && focusIdx < renders.length) {
        e.preventDefault();
        const r = renders[focusIdx];
        if (r.status === "ready") handlePlay(r);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, renders, focusIdx]);

  // Reset focus on close
  useEffect(() => {
    if (!open) setFocusIdx(-1);
  }, [open]);

  const completedRenders = renders.filter(r => r.status === "ready");

  const handleSelect = useCallback(async (renderId: string) => {
    try {
      await castsApi.selectRender(castId, renderId);
      setRenders(prev => prev.map(r => ({
        ...r,
        is_selected: r.id === renderId,
      })));
      toast({ title: "Render selected for publishing" });
    } catch (err: any) {
      toast({ title: "Failed to select render", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    }
  }, [castId]);

  const handlePlay = useCallback((render: RenderItem) => {
    setPlayingRender(render);
    setOpen(false);
  }, []);

  const handleDownload = useCallback((render: RenderItem) => {
    if (!render.output_video_r2_key) return;
    const a = document.createElement("a");
    a.href = cdnUrl(render.output_video_r2_key);
    const ql = render.quality === "hd_plus" ? "HD+" : render.quality === "hd" ? "HD" : "Simple";
    a.download = `render-v${render.version || "1"}-${ql}.mp4`;
    a.target = "_blank";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, []);

  const formatDuration = (seconds?: number) => {
    if (!seconds) return "--";
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  // Dropdown — portaled
  const dropdown = dropdownPos && open && createPortal(
    <div
      ref={portalRef}
      className="border border-white/10 rounded-xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-150"
      style={{
        position: "fixed",
        top: dropdownPos.top,
        left: dropdownPos.left,
        width: dropdownPos.width,
        maxHeight: dropdownPos.maxHeight,
        backgroundColor: "#0d0d0d",
        zIndex: 10000,
      }}
      onMouseEnter={enter}
      onMouseLeave={leave}
    >
      <div className="px-4 py-2.5 border-b border-white/5 text-[11px] text-white/30 uppercase tracking-wider sticky top-0" style={{ backgroundColor: '#0d0d0d' }}>
        Render History
      </div>
      <div className="max-h-[360px] overflow-y-auto">
        {loading ? (
          <div className="px-4 py-3 flex items-center gap-2 text-xs text-white/40">
            <Loader2 className="w-3 h-3 animate-spin" /> Loading...
          </div>
        ) : renders.length === 0 ? (
          <div className="px-4 py-3 text-xs text-white/30">No renders yet</div>
        ) : (
          renders.map((r, idx) => {
            const badge = STATUS_BADGE[r.status] || STATUS_BADGE.queued;
            return (
              <div
                key={r.id}
                className={`flex items-center gap-3 px-4 py-3 cursor-pointer group transition-all border-b border-white/[0.03] last:border-0
                  hover:bg-white/10 hover:border-l-2 hover:border-l-white/30 hover:pl-[14px]
                  ${justCompletedId === r.id ? "bg-green-500/10 border-l-2 border-l-green-500 pl-[14px] animate-pulse" : ""}
                  ${focusIdx === idx ? "ring-1 ring-accent bg-white/10" : ""}
                `}
                onClick={() => r.status === "ready" && handlePlay(r)}
              >
                {/* Thumbnail / play button — 56x56 */}
                <button
                  onClick={() => r.status === "ready" && handlePlay(r)}
                  disabled={r.status !== "ready"}
                  className="relative w-14 h-14 rounded bg-black/50 flex items-center justify-center shrink-0 overflow-hidden border border-white/10"
                >
                  {r.thumbnail_key ? (
                    <img src={cdnUrl(r.thumbnail_key)} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <Film className="w-4 h-4 text-white/30" />
                  )}
                  {r.status === "ready" && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                      <Play className="w-4 h-4 text-white" />
                    </div>
                  )}
                </button>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${badge.color}`}>
                      {badge.label}
                    </span>
                    {r.quality && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                        r.quality === "hd_plus" ? "bg-purple-500/20 text-purple-300 border-purple-500/30" :
                        r.quality === "hd" ? "bg-blue-500/20 text-blue-300 border-blue-500/30" :
                        "bg-white/10 text-white/50 border-white/10"
                      }`}>
                        {r.quality === "hd_plus" ? "HD+" : r.quality === "hd" ? "HD" : "Simple"}
                      </span>
                    )}
                    {r.version != null && (
                      <span className="text-[10px] text-white/40">v{r.version}</span>
                    )}
                    {justCompletedId === r.id && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-300">New</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-[11px] text-white/40 mt-1">
                    <span>{formatDuration(r.duration_seconds)}</span>
                    <span>
                      {r.completed_at
                        ? new Date(r.completed_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                        : r.created_at
                        ? new Date(r.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                        : ""}
                    </span>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1">
                  {r.status === "ready" && r.output_video_r2_key && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDownload(r); }}
                      className="p-1.5 rounded text-white/20 hover:text-white/60 transition-colors"
                      title="Download"
                    >
                      <Download className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); handleSelect(r.id); }}
                    className={`p-1.5 rounded transition-colors ${
                      r.is_selected
                        ? "text-yellow-400"
                        : "text-white/20 hover:text-yellow-400/60"
                    }`}
                    title={r.is_selected ? "Selected for publishing" : "Select for publishing"}
                  >
                    <Star className={`w-4 h-4 ${r.is_selected ? "fill-yellow-400" : ""}`} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>,
    document.body
  );

  return (
    <>
      <div ref={triggerRef} onMouseEnter={enter} onMouseLeave={leave}>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => { setOpen(v => !v); if (!open) fetchRenders(); }}
          className={`gap-1.5 text-xs h-8 transition-colors ${open ? 'text-accent bg-accent/10' : 'text-white/60 hover:text-white'}`}
          data-testid="renders-collection-btn"
        >
          <Film className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">
            Renders{completedRenders.length > 0 ? ` (${completedRenders.length})` : ""}
          </span>
        </Button>
      </div>

      {dropdown}

      {/* Video playback — production-grade RenderPlayer */}
      {playingRender && playingRender.output_video_r2_key && (
        <RenderPlayer
          renderId={playingRender.id}
          videoKey={playingRender.output_video_r2_key}
          version={playingRender.version}
          quality={playingRender.quality}
          onClose={() => setPlayingRender(null)}
        />
      )}
    </>
  );
});
