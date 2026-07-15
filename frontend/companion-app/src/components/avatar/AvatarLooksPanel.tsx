import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Star, Trash2, Plus, Loader2 } from "lucide-react";
import { avatarLooksApi } from "@/lib/api";
import type { AvatarLook } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { AddLookDialog } from "./AddLookDialog";
import { toast } from "@/hooks/useToast";

interface Props {
  avatarId: string;
}

export function AvatarLooksPanel({ avatarId }: Props) {
  const qc = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["avatar-looks", avatarId],
    queryFn: () => avatarLooksApi.list(avatarId),
    refetchInterval: (q) => {
      const looks: AvatarLook[] = q.state.data?.looks || [];
      return looks.some((l) => l.status === "pending" || l.status === "generating") ? 3000 : false;
    },
  });

  const looks: AvatarLook[] = data?.looks || [];

  const setDefaultMutation = useMutation({
    mutationFn: (lookId: string) => avatarLooksApi.setDefault(avatarId, lookId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (lookId: string) => avatarLooksApi.delete(avatarId, lookId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] });
      toast({ title: "Look deleted" });
    },
    onError: (err: any) =>
      toast({ title: "Delete failed", description: err?.response?.data?.detail || "Unknown error", variant: "destructive" }),
  });

  return (
    <div className="space-y-3 mt-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Looks ({looks.length})</h3>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="w-3.5 h-3.5 mr-1" /> Add Look
        </Button>
      </div>

      {isLoading ? (
        <div className="text-text-muted text-sm">Loading...</div>
      ) : looks.length === 0 ? (
        <div className="text-text-muted text-sm">No looks yet</div>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          {looks.map((look) => (
            <div key={look.id} className="border border-border rounded-lg overflow-hidden">
              <div className="relative aspect-9/16 bg-black">
                {look.image_url && look.status === "ready" ? (
                  <img src={look.image_url} alt={look.name} className="w-full h-full object-cover" />
                ) : look.status === "generating" || look.status === "pending" ? (
                  <div className="w-full h-full flex items-center justify-center text-text-muted">
                    <Loader2 className="w-6 h-6 animate-spin" />
                  </div>
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-red-400 text-xs p-2 text-center">
                    Failed: {look.error_message}
                  </div>
                )}
                {look.is_default && (
                  <div className="absolute top-1 right-1 bg-yellow-500 text-black text-xs px-1.5 py-0.5 rounded flex items-center">
                    <Star className="w-3 h-3 mr-0.5 fill-current" /> Default
                  </div>
                )}
              </div>
              <div className="p-2">
                <div className="text-xs font-medium truncate">{look.name}</div>
                <div className="flex items-center gap-1 mt-1.5">
                  {!look.is_default && look.status === "ready" && (
                    <button onClick={() => setDefaultMutation.mutate(look.id)} className="text-xs text-text-muted hover:text-text" title="Set as default">
                      <Star className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {!look.is_default && (
                    <button onClick={() => deleteMutation.mutate(look.id)} className="text-xs text-red-500 hover:text-red-400" title="Delete">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <AddLookDialog avatarId={avatarId} open={addOpen} onOpenChange={setAddOpen} />
    </div>
  );
}
