import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Code2, FileText, Pencil, Save, X, Clock, Hash } from "lucide-react";
import { adminApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";

export function PromptAdminPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-prompts"],
    queryFn: () => adminApi.prompts(),
  });

  // Track which prompt is currently being edited (by key)
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [changeReason, setChangeReason] = useState("");

  const updateMutation = useMutation({
    mutationFn: ({ key, system_prompt, change_reason }: { key: string; system_prompt: string; change_reason: string }) =>
      adminApi.updatePrompt(key, { system_prompt, change_reason }),
    onSuccess: (_data, variables) => {
      toast({ title: "Prompt updated", description: `"${variables.key}" saved successfully.` });
      queryClient.invalidateQueries({ queryKey: ["admin-prompts"] });
      setEditingKey(null);
      setEditText("");
      setChangeReason("");
    },
    onError: (err: any) => {
      toast({
        title: "Failed to save prompt",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    },
  });

  const handleStartEdit = (key: string, currentText: string) => {
    setEditingKey(key);
    setEditText(currentText);
    setChangeReason("");
  };

  const handleCancel = () => {
    setEditingKey(null);
    setEditText("");
    setChangeReason("");
  };

  const handleSave = (key: string) => {
    if (!changeReason.trim()) {
      toast({ title: "Change reason required", description: "Please enter a reason for this change.", variant: "destructive" });
      return;
    }
    updateMutation.mutate({ key, system_prompt: editText, change_reason: changeReason });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-accent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-20 text-center text-danger text-sm">
        Failed to load prompts. You may need admin access.
      </div>
    );
  }

  const prompts = data?.prompts || [];

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Code2 className="h-6 w-6 text-accent" />
          AI Prompt Registry
        </h1>
        <p className="text-sm text-text-muted mt-1">
          {prompts.length} prompts in use across the platform
        </p>
      </div>

      <div className="space-y-4">
        {prompts.map((p) => {
          const isEditing = editingKey === p.key;

          return (
            <div
              key={p.key}
              className="rounded-xl border border-border bg-surface p-5 space-y-3"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <code className="text-xs font-mono text-accent bg-accent/10 px-2 py-0.5 rounded">
                    {p.key}
                  </code>
                  <h3 className="mt-1.5 text-sm font-semibold text-text">{p.name}</h3>
                  <p className="text-xs text-text-muted mt-0.5">{p.description}</p>
                </div>
                <div className="shrink-0 text-right space-y-1">
                  <span className="text-[10px] text-text-muted block">
                    {p.character_count.toLocaleString()} chars
                  </span>
                  {/* 6.5 — Version and last updated info */}
                  <span className="text-[10px] text-text-muted flex items-center gap-1 justify-end">
                    <Hash className="h-2.5 w-2.5" />
                    v{p.version}
                  </span>
                  {p.last_updated && (
                    <span className="text-[10px] text-text-muted flex items-center gap-1 justify-end">
                      <Clock className="h-2.5 w-2.5" />
                      {new Date(p.last_updated).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>

              {/* 6.5 — Read-only display or editable textarea */}
              {isEditing ? (
                <div className="space-y-3">
                  <textarea
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    rows={12}
                    className="w-full rounded-lg bg-bg border border-accent/30 p-3 text-[11px] leading-relaxed text-text-dim font-mono resize-y focus:outline-none focus:border-accent/60"
                    placeholder="Enter system prompt text..."
                  />
                  <div className="flex items-center gap-2">
                    <input
                      value={changeReason}
                      onChange={e => setChangeReason(e.target.value)}
                      onKeyDown={e => e.key === "Enter" && handleSave(p.key)}
                      placeholder="Change reason (required)..."
                      className="flex-1 rounded-lg bg-bg border border-border px-3 py-2 text-xs text-text placeholder:text-text-muted focus:outline-none focus:border-accent/50"
                    />
                    <button
                      onClick={() => handleSave(p.key)}
                      disabled={updateMutation.isPending || !changeReason.trim()}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-accent text-white text-xs font-medium hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {updateMutation.isPending ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Save className="h-3 w-3" />
                      )}
                      Save
                    </button>
                    <button
                      onClick={handleCancel}
                      disabled={updateMutation.isPending}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-text-muted text-xs hover:bg-bg disabled:opacity-50"
                    >
                      <X className="h-3 w-3" />
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="relative group">
                  <div className="rounded-lg bg-bg border border-border p-3 max-h-48 overflow-y-auto">
                    <pre className="text-[11px] leading-relaxed text-text-dim whitespace-pre-wrap font-mono">
                      {p.system_prompt}
                    </pre>
                  </div>
                  {/* 6.5 — Edit button */}
                  <button
                    onClick={() => handleStartEdit(p.key, p.system_prompt)}
                    className="absolute top-2 right-2 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface border border-border text-text-muted text-[11px] opacity-0 group-hover:opacity-100 hover:text-accent hover:border-accent/30 transition-all"
                  >
                    <Pencil className="h-3 w-3" />
                    Edit
                  </button>
                </div>
              )}

              {p.used_in && (
                <div className="flex items-center gap-1.5 text-[10px] text-text-muted">
                  <FileText className="h-3 w-3" />
                  Used in: {p.used_in}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
