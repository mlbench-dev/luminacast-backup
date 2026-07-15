/**
 * ReadyPhase — Shown when a render completes.
 *
 * Video player with the final MP4
 * Download button
 * "Back to Editor" → setPhase("editor") (edits intact in twick_data)
 * "Edit Script (new version)" → snapshots current as v{N}, increments, goes to Script phase
 * Shows selected render from the renders collection
 */
import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Edit3, Play, Download, ArrowLeft, FilePlus, Star, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { castsApi } from "@/lib/api";
import { cdnUrl } from "@/lib/cdn";
import { toast } from "@/hooks/useToast";
import type { Cast } from "@/lib/types";

interface RenderItem {
  id: string;
  status: string;
  version?: number;
  quality?: string;
  duration_seconds?: number;
  is_selected: boolean;
  output_video_r2_key?: string;
  created_at?: string;
  completed_at?: string;
}

interface ReadyPhaseProps {
  cast: Cast;
  onEdit: () => void;
  onEditScript?: () => void;
}

export function ReadyPhase({ cast, onEdit, onEditScript }: ReadyPhaseProps) {
  const navigate = useNavigate();
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [renders, setRenders] = useState<RenderItem[]>([]);
  const [forking, setForking] = useState(false);

  // Fetch renders to find the selected one
  useEffect(() => {
    if (!cast?.id) return;
    castsApi.listRenders(cast.id).then((data: any) => {
      setRenders(Array.isArray(data) ? data : []);
    }).catch(() => {});
  }, [cast?.id]);

  // Find the best video URL: prefer selected render, then fallback to variant videos
  const selectedRender = renders.find(r => r.is_selected && r.status === "ready");
  const latestReadyRender = renders.find(r => r.status === "ready");

  const videoUrl = useMemo(() => {
    // Prefer selected render's output
    const renderKey = selectedRender?.output_video_r2_key || latestReadyRender?.output_video_r2_key;
    if (renderKey) return cdnUrl(renderKey);

    // Fallback to variant videos
    for (const block of cast.blocks || []) {
      for (const variant of block.variants || []) {
        if (variant.final_video_key) return cdnUrl(variant.final_video_key);
        if (variant.stream_url) return variant.stream_url;
        if (variant.clip_url) return variant.clip_url;
        if (variant.video_key) return cdnUrl(variant.video_key);
      }
    }
    return null;
  }, [cast, selectedRender, latestReadyRender]);

  const handlePlay = useCallback(() => {
    if (videoRef.current) {
      if (videoRef.current.paused) {
        videoRef.current.play();
      } else {
        videoRef.current.pause();
      }
    }
  }, []);

  const handleDownload = useCallback(() => {
    if (videoUrl) {
      const a = document.createElement("a");
      a.href = videoUrl;
      a.download = `${cast.name || "cast"}.mp4`;
      a.click();
    }
  }, [videoUrl, cast.name]);

  const handleEditScriptNewVersion = useCallback(async () => {
    if (!cast?.id || !onEditScript) return;
    setForking(true);
    try {
      await castsApi.fork(cast.id);
      toast({ title: "New version created", description: "Script editor opened for editing." });
      onEditScript();
    } catch (err: any) {
      toast({ title: "Failed to create new version", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    } finally {
      setForking(false);
    }
  }, [cast, onEditScript]);

  const activeRender = selectedRender || latestReadyRender;

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-green-500/20 flex items-center justify-center">
          <CheckCircle2 className="w-5 h-5 text-green-400" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-white">Cast Ready!</h2>
          <p className="text-sm text-white/50">{cast.name || "Untitled Cast"}</p>
        </div>
        {activeRender && (
          <div className="flex items-center gap-2 text-xs text-white/40">
            {activeRender.is_selected && <Star className="w-3.5 h-3.5 text-yellow-400 fill-yellow-400" />}
            {activeRender.quality && (
              <span className="px-1.5 py-0.5 rounded bg-white/10">{activeRender.quality}</span>
            )}
            {activeRender.version && <span>v{activeRender.version}</span>}
          </div>
        )}
      </div>

      {/* Video Player */}
      <div className="rounded-xl overflow-hidden bg-black border border-white/10 relative group">
        {videoUrl ? (
          <>
            <video
              ref={videoRef}
              src={videoUrl}
              controls
              autoPlay={false}
              playsInline
              className="w-full aspect-9/16 max-h-[70vh] object-contain bg-black"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              data-testid="ready-video-player"
            />
            {/* Play button overlay when paused */}
            {!playing && (
              <button
                onClick={handlePlay}
                className="absolute inset-0 flex items-center justify-center bg-black/30 transition-opacity group-hover:bg-black/40"
                data-testid="ready-play-overlay"
              >
                <div className="w-16 h-16 rounded-full bg-white/20 backdrop-blur-xs flex items-center justify-center">
                  <Play className="w-8 h-8 text-white ml-1" />
                </div>
              </button>
            )}
          </>
        ) : (
          <div className="w-full aspect-9/16 max-h-[70vh] flex items-center justify-center text-white/40">
            <div className="text-center space-y-2">
              <Play className="w-8 h-8 mx-auto" />
              <p className="text-sm">No video available yet</p>
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-col sm:flex-row items-center gap-3 justify-center">
        {videoUrl && (
          <Button onClick={handleDownload} className="bg-accent hover:bg-accent-hover text-white">
            <Download className="w-4 h-4 mr-2" />
            Download MP4
          </Button>
        )}
        <Button variant="outline" onClick={onEdit} className="border-white/20">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Editor
        </Button>
        {onEditScript && (
          <Button variant="outline" onClick={handleEditScriptNewVersion} disabled={forking} className="border-white/20">
            <FilePlus className="w-4 h-4 mr-2" />
            {forking ? "Creating version..." : "Edit Script (new version)"}
          </Button>
        )}
        {/* Publish to social platforms via Zernio. Drops the user on the
            Publish page pre-filled with the latest ready render and an
            AI-generated caption. */}
        {videoUrl && (
          <Button
            onClick={() => navigate(`/publish/${cast.id}`)}
            className="bg-accent hover:bg-accent-hover text-white"
          >
            <Send className="w-4 h-4 mr-2" />
            Publish
          </Button>
        )}
      </div>
    </div>
  );
}
