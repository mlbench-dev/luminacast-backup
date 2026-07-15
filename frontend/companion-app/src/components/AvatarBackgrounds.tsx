import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Loader2, Trash2, Wand2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/hooks/useToast";
import { api } from "@/lib/api";
import { confirmAction } from "@/lib/swal";

interface AvatarBackground {
  id: string;
  name: string;
  url: string;
  thumbnail_url: string;
  source: string;
  position: number;
  created_at: string;
}

export function AvatarBackgrounds({ avatarId }: { avatarId: string }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [genPrompt, setGenPrompt] = useState("");
  const [showGenModal, setShowGenModal] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["avatar-backgrounds", avatarId],
    queryFn: () =>
      api.get<{ backgrounds: AvatarBackground[] }>(`/avatars/${avatarId}/backgrounds`).then((r) => r.data),
    enabled: !!avatarId,
  });

  const backgrounds = data?.backgrounds ?? [];

  const uploadMutation = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.post(`/avatars/${avatarId}/backgrounds/upload`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["avatar-backgrounds", avatarId] });
      toast({ title: "Scene uploaded", variant: "success" });
    },
    onError: () => toast({ title: "Upload failed", variant: "destructive" }),
  });

  const generateMutation = useMutation({
    mutationFn: (prompt: string) =>
      api.post(`/avatars/${avatarId}/backgrounds/generate`, { prompt }).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["avatar-backgrounds", avatarId] });
      setShowGenModal(false);
      setGenPrompt("");
      toast({ title: "Scene generated", variant: "success" });
    },
    onError: () => toast({ title: "Generation failed", variant: "destructive" }),
  });

  const renameMutation = useMutation({
    mutationFn: ({ bgId, name }: { bgId: string; name: string }) =>
      api.patch(`/avatars/${avatarId}/backgrounds/${bgId}`, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["avatar-backgrounds", avatarId] });
      setEditingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (bgId: string) => api.delete(`/avatars/${avatarId}/backgrounds/${bgId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["avatar-backgrounds", avatarId] });
      toast({ title: "Scene deleted", variant: "success" });
    },
  });

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadMutation.mutate(file);
    e.target.value = "";
  };

  const startRename = (bg: AvatarBackground) => {
    setEditingId(bg.id);
    setEditName(bg.name);
  };

  const commitRename = (bgId: string) => {
    if (editName.trim()) {
      renameMutation.mutate({ bgId, name: editName.trim() });
    }
    setEditingId(null);
  };

  return (
    <div className="mt-4">
      <h4 className="text-sm font-semibold text-text mb-2">Scenes</h4>

      <div className="flex gap-3 overflow-x-auto pb-2">
        {backgrounds.map((bg) => (
          <div
            key={bg.id}
            className="shrink-0 w-[100px] rounded-lg border border-border bg-surface overflow-hidden group"
          >
            <div className="relative aspect-9/16">
              <img
                src={bg.thumbnail_url || bg.url}
                alt={bg.name}
                className="w-full h-full object-cover"
              />
              <button
                onClick={async () => {
                  if (await confirmAction({
                    title: "Delete this scene?",
                    text: "This scene will be removed from the avatar.",
                    confirmButtonText: "Delete",
                  })) deleteMutation.mutate(bg.id);
                }}
                className="absolute top-1 right-1 p-1 rounded bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <Trash2 className="h-3 w-3 text-white" />
              </button>
            </div>
            {editingId === bg.id ? (
              <input
                className="w-full text-[10px] px-1 py-0.5 bg-card text-text border-0 outline-hidden"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                onBlur={() => commitRename(bg.id)}
                onKeyDown={(e) => e.key === "Enter" && commitRename(bg.id)}
                autoFocus
              />
            ) : (
              <div
                className="text-[10px] text-text-muted px-1 py-0.5 truncate cursor-pointer hover:text-text"
                onClick={() => startRename(bg)}
                title="Click to rename"
              >
                {bg.name}
              </div>
            )}
          </div>
        ))}

        {/* Action buttons */}
        <div className="shrink-0 flex flex-col gap-2 justify-center">
          <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleFileSelect} />
          <Button
            size="sm"
            variant="outline"
            className="text-xs whitespace-nowrap"
            onClick={() => fileRef.current?.click()}
            disabled={uploadMutation.isPending}
          >
            {uploadMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Upload className="h-3 w-3 mr-1" />}
            Upload
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-xs whitespace-nowrap"
            onClick={() => setShowGenModal(true)}
          >
            <Wand2 className="h-3 w-3 mr-1" /> Generate
          </Button>
        </div>
      </div>

      {isLoading && <p className="text-xs text-text-muted">Loading scenes...</p>}
      {!isLoading && backgrounds.length === 0 && (
        <p className="text-xs text-text-muted">No scenes yet. Upload or generate one.</p>
      )}

      {/* Generate modal */}
      {showGenModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-surface border border-border rounded-lg p-4 w-80">
            <h4 className="text-sm font-semibold text-text mb-2">Generate Scene</h4>
            <Input
              value={genPrompt}
              onChange={(e) => setGenPrompt(e.target.value)}
              placeholder="e.g., minimalist studio, warm wood"
              className="mb-3 text-xs"
              onKeyDown={(e) => e.key === "Enter" && genPrompt.trim() && generateMutation.mutate(genPrompt)}
            />
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="outline" onClick={() => setShowGenModal(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={!genPrompt.trim() || generateMutation.isPending}
                onClick={() => generateMutation.mutate(genPrompt)}
              >
                {generateMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Wand2 className="h-3 w-3 mr-1" />}
                Generate
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
