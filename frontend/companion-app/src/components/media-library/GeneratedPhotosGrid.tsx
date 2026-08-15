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
} from "lucide-react";

interface GeneratedPhoto {
  id: string;
  url: string;
  prompt: string;
  negative_prompt: string | null;
  width: number;
  height: number;
  seed: number | null;
  engine_used: string;
  tier: number;
  created_at: string;
}

interface GeneratedPhotosGridProps {
  onRegenerateClick: (prompt: string, negativePrompt?: string) => void;
}

export function GeneratedPhotosGrid({
  onRegenerateClick,
}: GeneratedPhotosGridProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [selectedPhoto, setSelectedPhoto] = useState<GeneratedPhoto | null>(
    null
  );

  const { data, isLoading } = useQuery({
    queryKey: ["generated-photos"],
    queryFn: () =>
      api
        .get<{ photos: GeneratedPhoto[]; limit: number; offset: number }>(
          "/photos/generated?limit=50"
        )
        .then((r) => r.data),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      api.delete(`/photos/generated/${id}`).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["generated-photos"] });
      setSelectedPhoto(null);
      toast({ title: "Photo deleted" });
    },
    onError: (e: Error) =>
      toast({
        title: "Delete failed",
        description: e.message,
        variant: "destructive",
      }),
  });

  const photos: GeneratedPhoto[] = data?.photos || [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-text-dim" />
      </div>
    );
  }

  if (photos.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface py-20">
        <Sparkles className="mb-4 h-16 w-16 text-text-muted" />
        <h2 className="text-lg font-semibold text-text">
          No generated photos yet
        </h2>
        <p className="mt-1 text-sm text-text-dim">
          Click Generate with AI to create one.
        </p>
      </div>
    );
  }

  return (
    <>
      {/* Detail drawer */}
      {selectedPhoto && (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-end bg-black/70 backdrop-blur-xs"
          onClick={() => setSelectedPhoto(null)}
        >
          <div
            className="relative w-full max-w-xl overflow-y-auto bg-zinc-900 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setSelectedPhoto(null)}
              className="absolute right-3 top-3 z-10 rounded-full bg-black/50 p-1.5 text-white hover:bg-black/70"
            >
              <X className="h-4 w-4" />
            </button>

            <img
              src={selectedPhoto.url}
              alt={selectedPhoto.prompt}
              className="w-full object-contain"
            />

            <div className="space-y-4 p-5">
              <div>
                <h3 className="text-xs font-medium uppercase text-text-dim">
                  Prompt
                </h3>
                <p className="mt-1 text-sm text-text">{selectedPhoto.prompt}</p>
              </div>

              {selectedPhoto.negative_prompt && (
                <div>
                  <h3 className="text-xs font-medium uppercase text-text-dim">
                    Negative prompt
                  </h3>
                  <p className="mt-1 text-sm text-text-dim">
                    {selectedPhoto.negative_prompt}
                  </p>
                </div>
              )}

              <div className="flex flex-wrap gap-3 text-xs text-text-dim">
                <span>
                  {selectedPhoto.width}&times;{selectedPhoto.height}
                </span>
                {selectedPhoto.seed !== null && (
                  <span>Seed: {selectedPhoto.seed}</span>
                )}
                <span>
                  Tier {selectedPhoto.tier}
                </span>
                <span>
                  {new Date(selectedPhoto.created_at).toLocaleString()}
                </span>
              </div>

              <div className="flex gap-2 pt-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    onRegenerateClick(
                      selectedPhoto.prompt,
                      selectedPhoto.negative_prompt || undefined
                    );
                    setSelectedPhoto(null);
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
                    a.href = selectedPhoto.url;
                    a.download = `generated_${selectedPhoto.id}.jpg`;
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
                    navigator.clipboard.writeText(selectedPhoto.prompt);
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
                    if (confirm("Delete this generated photo?"))
                      deleteMutation.mutate(selectedPhoto.id);
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
        {photos.map((photo) => (
          <div
            key={photo.id}
            className="group relative cursor-pointer overflow-hidden rounded-lg border border-border bg-surface transition-colors hover:border-accent/40"
            onClick={() => setSelectedPhoto(photo)}
          >
            <div className="relative aspect-square bg-black">
              <img
                src={photo.url}
                alt={photo.prompt}
                className="h-full w-full object-cover"
                loading="lazy"
              />
              {/* AI badge */}
              <span className="absolute right-2 top-2 rounded bg-accent/80 px-1.5 py-0.5 text-[10px] font-bold text-white">
                AI
              </span>
              {/* Expand button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setSelectedPhoto(photo);
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
                title={photo.prompt}
              >
                {photo.prompt}
              </p>
              <div className="mt-1 flex items-center gap-3 text-xs text-text-dim">
                <span>
                  {photo.width}&times;{photo.height}
                </span>
                <span>Tier {photo.tier}</span>
              </div>
            </div>

            {/* Delete button on hover */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (confirm("Delete this photo?"))
                  deleteMutation.mutate(photo.id);
              }}
              className="absolute right-2 top-8 rounded-full bg-black/60 p-1.5 text-white opacity-0 transition-opacity hover:bg-red-600 group-hover:opacity-100"
              title="Delete photo"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
