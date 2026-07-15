import { useState, useRef, useCallback } from "react";
import { Upload, Loader2, FileVideo } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { avatarApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";

const ACCEPTED = ".mp4,.mov,.webm";
const MAX_SIZE = 200 * 1024 * 1024;

interface UploadZoneProps {
  onUploaded: (data: { video_r2_key: string; video_url: string; duration_seconds: number }) => void;
}

export function UploadZone({ onUploaded }: UploadZoneProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);

  const handleFile = useCallback(async (file: File) => {
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!["mp4", "mov", "webm"].includes(ext || "")) {
      toast({ title: "Unsupported format", description: "Please upload .mp4, .mov, or .webm", variant: "destructive" });
      return;
    }
    if (file.size > MAX_SIZE) {
      toast({ title: "File too large", description: "Maximum 200 MB", variant: "destructive" });
      return;
    }

    setIsUploading(true);
    setProgress(10);
    try {
      const interval = setInterval(() => setProgress((p) => Math.min(p + 5, 90)), 500);
      const data = await avatarApi.uploadVideo(file);
      clearInterval(interval);
      setProgress(100);
      onUploaded(data);
    } catch (err: any) {
      toast({
        title: "Upload failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    } finally {
      setIsUploading(false);
      setProgress(0);
    }
  }, [onUploaded]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  return (
    <div className="space-y-4" data-testid="upload-zone">
      <input
        ref={fileRef}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
        }}
      />

      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => !isUploading && fileRef.current?.click()}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-10 transition-all cursor-pointer",
          dragOver ? "border-accent bg-accent/10" : "border-border hover:border-accent/60 hover:bg-accent/5",
          isUploading && "pointer-events-none opacity-60",
        )}
        data-testid="upload-dropzone"
      >
        {isUploading ? (
          <Loader2 className="h-10 w-10 text-accent animate-spin" />
        ) : (
          <FileVideo className="h-10 w-10 text-text-muted" />
        )}
        <div className="text-center">
          <p className="text-sm font-medium text-text">
            {dragOver ? "Drop your video here" : "Drag and drop your video here"}
          </p>
          <p className="mt-1 text-xs text-text-muted">or click to browse</p>
          <p className="mt-2 text-[10px] text-text-muted">.mp4, .mov, .webm — max 200 MB, 15s to 3 min</p>
        </div>
      </div>

      {isUploading && (
        <div className="space-y-2">
          <Progress value={progress} className="h-2" />
          <p className="text-xs text-text-muted text-center">Uploading video... {progress}%</p>
        </div>
      )}

      <div className="rounded-lg bg-surface/50 border border-border p-3 space-y-1">
        <p className="text-xs font-medium text-text-dim">Tips for best results</p>
        <ul className="space-y-0.5 text-[10px] text-text-muted">
          <li>• Face the camera directly with good lighting</li>
          <li>• Speak naturally for 30-60 seconds</li>
          <li>• Avoid background music</li>
          <li>• Indoor setting with minimal echo</li>
        </ul>
      </div>
    </div>
  );
}
