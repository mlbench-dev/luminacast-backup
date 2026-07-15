import { useState, useEffect } from "react";
import { userVideosApi } from "@/lib/api";
import type { UserVideoAsset } from "@/lib/types";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Film, Check, Loader2 } from "lucide-react";

interface UserVideoPickerDialogProps {
  open: boolean;
  onClose: () => void;
  onSelect: (video: UserVideoAsset) => void;
  selectedId?: string | null;
}

export function UserVideoPickerDialog({
  open,
  onClose,
  onSelect,
  selectedId,
}: UserVideoPickerDialogProps) {
  const [videos, setVideos] = useState<UserVideoAsset[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    userVideosApi
      .list()
      .then((data: { videos: UserVideoAsset[] }) => setVideos(data.videos || []))
      .catch(() => setVideos([]))
      .finally(() => setLoading(false));
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-zinc-900 border-white/10 max-w-md max-h-[70vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-white">Select a Video</DialogTitle>
        </DialogHeader>

        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-purple-400" />
          </div>
        )}

        {!loading && videos.length === 0 && (
          <div className="text-center py-8 text-white/50 text-sm">
            No videos uploaded yet. Go to My Videos | Photos to upload one.
          </div>
        )}

        {!loading && videos.length > 0 && (
          <div className="space-y-2">
            {videos.map((v) => (
              <button
                key={v.id}
                onClick={() => {
                  onSelect(v);
                  onClose();
                }}
                className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors text-left ${
                  selectedId === v.id
                    ? "border-purple-500 bg-purple-500/10"
                    : "border-white/10 bg-white/5 hover:bg-white/10"
                }`}
              >
                <Film className="w-5 h-5 text-purple-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white truncate">
                    {v.name || v.original_filename}
                  </div>
                  <div className="text-xs text-white/40">
                    {v.duration_seconds
                      ? `${Math.round(v.duration_seconds)}s`
                      : "Unknown duration"}
                    {v.width && v.height ? ` · ${v.width}×${v.height}` : ""}
                  </div>
                </div>
                {selectedId === v.id && (
                  <Check className="w-4 h-4 text-purple-400 shrink-0" />
                )}
              </button>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
