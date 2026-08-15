import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import {
  Loader2,
  Trash2,
  Maximize2,
  X,
  Download,
  RefreshCw,
  Sparkles,
  Copy,
  Play,
} from "lucide-react";

interface GeneratedVideo {
  id: string;
  url: string;
  prompt: string;
  negative_prompt: string | null;
  engine_used: string;
  mode: string;
  duration: number;
  aspect_ratio: string;
  camera_preset: string | null;
  seed: number | null;
  batch_id: string | null;
  created_at: string;
}

interface GeneratedVideosGridProps {
  onRegenerateClick: (prompt: string, negativePrompt?: string) => void;
}

function engineLabel(engine: string): string {
  if (engine.includes("kling")) return "Kling";
  if (engine.includes("wan")) return "Wan";
  return engine;
}

export function GeneratedVideosGrid({
  onRegenerateClick,
}: GeneratedVideosGridProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [selectedVideo, setSelectedVideo] = useState<GeneratedVideo | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["generated-videos"],
    queryFn: () =>
      api
        .get<{ videos: GeneratedVideo[]; limit: number; offset: number }>(
          "/videos/generated?limit=50"
        )
        .then((r) => r.data),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      api.delete(`/videos/generated/${id}`).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["generated-videos"] });
      setSelectedVideo(null);
      toast({ title: "Video deleted" });
    },
    onError: (e: Error) =>
      toast({
        title: "Delete failed",
        description: e.message,
        variant: "destructive",
      }),
  });

  const videos: GeneratedVideo[] = data?.videos || [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-text-dim" />
      </div>
    );
  }

  if (videos.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface py-20">
        <Sparkles className="mb-4 h-16 w-16 text-text-muted" />
        <h2 className="text-lg font-semibold text-text">
          No generated videos yet
        </h2>
        <p className="mt-1 text-sm text-text-dim">
          Click Generate video with AI to create one.
        </p>
      </div>
    );
  }

  return (
    <>
      {/* Detail drawer */}
      {selectedVideo && (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-end bg-black/70 backdrop-blur-xs"
          onClick={() => setSelectedVideo(null)}
        >
          <div
            className="relative w-full max-w-xl overflow-y-auto bg-zinc-900 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setSelectedVideo(null)}
              className="absolute right-3 top-3 z-10 rounded-full bg-black/50 p-1.5 text-white hover:bg-black/70"
            >
              <X className="h-4 w-4" />
            </button>

            <video
              src={selectedVideo.url}
              className="w-full"
              controls
              autoPlay
            />

            <div className="space-y-4 p-5">
              <div>
                <h3 className="text-xs font-medium uppercase text-text-dim">
                  Prompt
                </h3>
                <p className="mt-1 text-sm text-text">{selectedVideo.prompt}</p>
              </div>

              {selectedVideo.negative_prompt && (
                <div>
                  <h3 className="text-xs font-medium uppercase text-text-dim">
                    Negative prompt
                  </h3>
                  <p className="mt-1 text-sm text-text-dim">
                    {selectedVideo.negative_prompt}
                  </p>
                </div>
              )}

              <div className="flex flex-wrap gap-3 text-xs text-text-dim">
                <span className="rounded bg-accent/20 px-1.5 py-0.5 font-medium text-accent">
                  {engineLabel(selectedVideo.engine_used)}
                </span>
                <span>{selectedVideo.mode === "text_to_video" ? "Text to Video" : "Image to Video"}</span>
                <span>{selectedVideo.duration}s</span>
                <span>{selectedVideo.aspect_ratio}</span>
                {selectedVideo.camera_preset && selectedVideo.camera_preset !== "none" && (
                  <span>Camera: {selectedVideo.camera_preset}</span>
                )}
                {selectedVideo.seed !== null && (
                  <span>Seed: {selectedVideo.seed}</span>
                )}
                {selectedVideo.batch_id && (
                  <span>Batch: {selectedVideo.batch_id}</span>
                )}
                <span>
                  {new Date(selectedVideo.created_at).toLocaleString()}
                </span>
              </div>

              <div className="flex gap-2 pt-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    onRegenerateClick(
                      selectedVideo.prompt,
                      selectedVideo.negative_prompt || undefined
                    );
                    setSelectedVideo(null);
                  }}
                >
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  Regenerate
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    const a = document.createElement("a");
                    a.href = selectedVideo.url;
                    a.download = `generated_${selectedVideo.id}.mp4`;
                    a.click();
                  }}
                >
                  <Download className="mr-1.5 h-3.5 w-3.5" />
                  Download
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    navigator.clipboard.writeText(selectedVideo.prompt);
                    toast({ title: "Prompt copied" });
                  }}
                >
                  <Copy className="mr-1.5 h-3.5 w-3.5" />
                  Copy prompt
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="text-red-400 hover:text-red-300"
                  onClick={() => {
                    if (confirm("Delete this generated video?"))
                      deleteMutation.mutate(selectedVideo.id);
                  }}
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  Delete
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {videos.map((video) => (
          <div
            key={video.id}
            className="group relative cursor-pointer overflow-hidden rounded-lg border border-border bg-surface transition-colors hover:border-accent/40"
            onClick={() => setSelectedVideo(video)}
          >
            <div className="relative aspect-video bg-black">
              <video
                src={video.url}
                className="h-full w-full object-cover"
                preload="metadata"
                muted
                onMouseEnter={(e) => (e.target as HTMLVideoElement).play().catch(() => {})}
                onMouseLeave={(e) => { const v = e.target as HTMLVideoElement; v.pause(); v.currentTime = 0; }}
              />
              {/* Play overlay */}
              <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                <div className="rounded-full bg-black/60 p-3">
                  <Play className="h-6 w-6 text-white" />
                </div>
              </div>
              {/* AI badge */}
              <span className="absolute right-2 top-2 rounded bg-accent/80 px-1.5 py-0.5 text-[10px] font-bold text-white">
                AI
              </span>
              {/* Engine badge */}
              <span className="absolute right-10 top-2 rounded bg-zinc-800/80 px-1.5 py-0.5 text-[10px] font-medium text-zinc-300">
                {engineLabel(video.engine_used)}
              </span>
              {/* Duration + aspect overlay */}
              <div className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 text-xs font-medium text-white">
                {video.duration}s {video.aspect_ratio}
              </div>
              {/* Expand button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedVideo(video);
                }}
                className="absolute bottom-2 left-2 rounded bg-black/60 p-1 text-white opacity-0 transition-opacity hover:bg-black/80 group-hover:opacity-100"
                title="View details"
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </button>
            </div>

            <div className="p-3">
              <p
                className="truncate text-sm text-text"
                title={video.prompt}
              >
                {video.prompt}
              </p>
              <div className="mt-1 flex items-center gap-3 text-xs text-text-dim">
                <span>{engineLabel(video.engine_used)}</span>
                <span>{video.mode === "text_to_video" ? "T2V" : "I2V"}</span>
                <span>{video.duration}s</span>
              </div>
            </div>

            {/* Delete button on hover */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (confirm("Delete this video?"))
                  deleteMutation.mutate(video.id);
              }}
              className="absolute right-2 top-8 rounded-full bg-black/60 p-1.5 text-white opacity-0 transition-opacity hover:bg-red-600 group-hover:opacity-100"
              title="Delete video"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
