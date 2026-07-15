import { useRef, useCallback, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { RadioTower, Loader2, AlertCircle, CheckCircle2, Clock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { liveReferenceApi } from "@/api/liveReferenceApi";
import type { LiveReference, LiveReferenceStatus } from "@/api/liveReferenceApi";
import { toast } from "@/hooks/useToast";

interface LiveReferenceCardProps {
  avatarId?: string;
  castId?: string;
  title?: string;
  subtitle?: string;
  helperText?: string;
}

const STATUS_COLORS: Record<LiveReferenceStatus, string> = {
  uploaded: "bg-amber-900/50 text-amber-400 border-amber-500/30",
  transcribing: "bg-blue-900/50 text-blue-400 border-blue-500/30",
  transcribed: "bg-blue-900/50 text-blue-400 border-blue-500/30",
  assessed: "bg-green-900/50 text-green-400 border-green-500/30",
  failed: "bg-red-900/50 text-red-400 border-red-500/30",
};

// User-facing stage labels — no engine/provider names leak here.
const STAGE_LABEL: Record<LiveReferenceStatus, string> = {
  uploaded: "Uploading",
  transcribing: "Transcribing",
  transcribed: "Building assessment",
  assessed: "Done",
  failed: "Failed",
};

const ACCEPT = "audio/*,video/*,.mp3,.wav,.m4a,.mp4,.mov,.webm";

function isActive(status: LiveReferenceStatus): boolean {
  return status === "uploaded" || status === "transcribing" || status === "transcribed";
}

function registerSummary(ref: LiveReference): string | null {
  const a = ref.assessment as Record<string, unknown> | null;
  if (!a) return null;
  const reg = a.register;
  return typeof reg === "string" && reg.trim() ? reg.trim() : null;
}

export function LiveReferenceCard({
  avatarId,
  castId,
  title = "Live Voice",
  subtitle = "Upload past live sessions to teach your selling style.",
  helperText = "Accepts .mp4, .mov, .webm, .m4a, .mp3, .wav",
}: LiveReferenceCardProps) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);

  const queryKey = ["live-references", avatarId ?? null, castId ?? null];

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => liveReferenceApi.listLiveReferences({ avatarId, castId }),
    enabled: !!(avatarId || castId),
    refetchInterval: (q) => {
      const refs: LiveReference[] = q.state.data?.live_references || [];
      return refs.some((r) => isActive(r.status)) ? 3000 : false;
    },
  });

  const refs: LiveReference[] = data?.live_references || [];

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      liveReferenceApi.uploadLiveReference({
        file,
        avatarId,
        castId,
        onProgress: (pct) => setUploadPct(pct),
      }),
    onSuccess: () => {
      setUploadPct(null);
      qc.invalidateQueries({ queryKey });
      toast({ title: "Processing live session..." });
    },
    onError: (err: any) => {
      setUploadPct(null);
      toast({
        title: "Upload failed",
        description: err?.response?.data?.detail || "Try again",
        variant: "destructive",
      });
    },
  });

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files) return;
      Array.from(files).forEach((file) => uploadMutation.mutate(file));
    },
    [uploadMutation],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const uploading = uploadMutation.isPending || uploadPct !== null;

  return (
    <div className="space-y-3" data-testid="live-reference-card">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/10">
          <RadioTower className="h-5 w-5 text-accent" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-text">{title}</h3>
          <p className="text-xs text-text-muted">{subtitle}</p>
        </div>
      </div>

      {/* Dropzone */}
      <div
        className={cn(
          "rounded-lg border-2 border-dashed border-border hover:border-accent/50 transition-colors p-6 text-center cursor-pointer",
          uploading && "opacity-60 pointer-events-none",
        )}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        data-testid="live-reference-dropzone"
      >
        {uploading ? (
          <div className="space-y-2">
            <Loader2 className="h-6 w-6 mx-auto text-accent animate-spin" />
            <p className="text-xs text-text-muted">
              Uploading{uploadPct !== null ? ` ${uploadPct}%` : "..."}
            </p>
          </div>
        ) : (
          <>
            <RadioTower className="h-7 w-7 mx-auto text-text-muted mb-2" />
            <p className="text-sm text-text-muted">Drop a past live recording, or click to upload</p>
            <p className="text-xs text-text-muted mt-1">{helperText}</p>
          </>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
          data-testid="live-reference-file-input"
        />
      </div>

      {/* Reference list */}
      {isLoading ? (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
        </div>
      ) : refs.length > 0 ? (
        <div className="space-y-2">
          {[...refs]
            .sort(
              (a, b) =>
                new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime(),
            )
            .map((ref) => {
              const summary = registerSummary(ref);
              return (
                <div
                  key={ref.id}
                  className="rounded-lg border border-border bg-surface p-3"
                  data-testid={`live-reference-${ref.id}`}
                >
                  <div className="flex items-center gap-3">
                    <Badge className={`text-[10px] border ${STATUS_COLORS[ref.status]}`}>
                      {STAGE_LABEL[ref.status]}
                    </Badge>

                    {ref.duration_seconds ? (
                      <span className="text-xs text-text-muted flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {Math.round(ref.duration_seconds / 60)}m
                      </span>
                    ) : null}

                    {isActive(ref.status) && (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
                    )}
                    {ref.status === "assessed" && (
                      <CheckCircle2 className="h-3.5 w-3.5 text-green-400 shrink-0" />
                    )}

                    <span className="text-xs flex-1 truncate text-text-muted">
                      {ref.status === "assessed" && summary
                        ? summary
                        : STAGE_LABEL[ref.status]}
                    </span>
                  </div>

                  {ref.status === "failed" && ref.error_message && (
                    <div className="mt-2 flex items-start gap-1.5 text-xs text-red-400">
                      <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                      {ref.error_message}
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      ) : null}
    </div>
  );
}
