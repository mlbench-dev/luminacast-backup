import { useState, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Loader2,
  Sparkles,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Upload,
  Play,
} from "lucide-react";

interface GenerateVideoWithAIModalProps {
  open: boolean;
  onClose: () => void;
  initialPrompt?: string;
  initialNegativePrompt?: string;
  onViewInLibrary?: () => void;
}

type Engine = "kling" | "wan";
type Mode = "text_to_video" | "image_to_video";

const ASPECT_RATIOS = ["16:9", "9:16", "1:1"] as const;
const DURATIONS = [5, 10] as const;
const NUM_OPTIONS = [1, 2, 4] as const;

const KLING_CAMERA_PRESETS = [
  { value: "none", label: "None" },
  { value: "down_back", label: "Pull Back" },
  { value: "forward_up", label: "Push Forward" },
  { value: "right_turn_forward", label: "Right Turn" },
  { value: "left_turn_forward", label: "Left Turn" },
] as const;

interface GeneratedResult {
  videos: Array<{
    id: string;
    url: string;
    prompt: string;
    engine_used: string;
    mode: string;
    duration: number;
    aspect_ratio: string;
    batch_id: string;
    created_at: string;
  }>;
  engine_used: string;
  batch_id: string;
  total_cost_usd: number;
}

function costEstimate(engine: Engine, duration: number, numVideos: number): string {
  const perVideo = engine === "kling"
    ? (duration === 5 ? 0.35 : 0.70)
    : (duration === 5 ? 0.25 : 0.50);
  return (perVideo * numVideos).toFixed(2);
}

function timeEstimate(engine: Engine, duration: number, numVideos: number): number {
  const base = engine === "kling"
    ? (duration === 5 ? 90 : 180)
    : (duration === 5 ? 60 : 120);
  return base * numVideos;
}

export function GenerateVideoWithAIModal({
  open,
  onClose,
  initialPrompt = "",
  initialNegativePrompt = "",
  onViewInLibrary,
}: GenerateVideoWithAIModalProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  const [engine, setEngine] = useState<Engine>("kling");
  const [mode, setMode] = useState<Mode>("text_to_video");
  const [prompt, setPrompt] = useState(initialPrompt);
  const [negativePrompt, setNegativePrompt] = useState(initialNegativePrompt);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [seed, setSeed] = useState<string>("");
  const [aspectRatio, setAspectRatio] = useState<typeof ASPECT_RATIOS[number]>("16:9");
  const [duration, setDuration] = useState<typeof DURATIONS[number]>(5);
  const [numVideos, setNumVideos] = useState<1 | 2 | 4>(1);
  const [cameraPreset, setCameraPreset] = useState("none");
  const [referenceImageUrl, setReferenceImageUrl] = useState<string | null>(null);
  const [result, setResult] = useState<GeneratedResult | null>(null);

  // Sync initial values when they change (e.g., from Regenerate)
  const [lastInitial, setLastInitial] = useState(initialPrompt);
  if (initialPrompt !== lastInitial) {
    setPrompt(initialPrompt);
    setNegativePrompt(initialNegativePrompt);
    setLastInitial(initialPrompt);
    setResult(null);
  }

  const generateMutation = useMutation({
    mutationFn: async () => {
      let finalPrompt = prompt;
      // Auto-enhance via Inspire Me if prompt is empty
      if (!finalPrompt.trim()) {
        try {
          const { data: inspireData } = await api.post<{ enhanced_prompt: string }>("/videos/inspire-me", {
            user_idea: "",
            engine,
            mode,
            duration,
            aspect_ratio: aspectRatio,
            camera_preset: engine === "kling" ? cameraPreset : null,
            reference_image_url: mode === "image_to_video" ? referenceImageUrl : null,
          });
          finalPrompt = inspireData.enhanced_prompt;
          setPrompt(finalPrompt);
        } catch {
          throw new Error("Failed to generate a prompt. Please type one manually.");
        }
      }
      return api
        .post<GeneratedResult>("/videos/generate", {
          engine,
          mode,
          prompt: finalPrompt,
          reference_image_url: mode === "image_to_video" ? referenceImageUrl : null,
          duration,
          aspect_ratio: aspectRatio,
          negative_prompt: negativePrompt || null,
          seed: seed ? parseInt(seed, 10) : null,
          camera_preset: engine === "kling" ? cameraPreset : null,
          num_videos: numVideos,
        })
        .then((r) => r.data);
    },
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ["generated-videos"] });
    },
    onError: (e: Error) => {
      toast({
        title: "Generation failed",
        description: e.message,
        variant: "destructive",
      });
    },
  });

  const inspireMutation = useMutation({
    mutationFn: () =>
      api
        .post<{ enhanced_prompt: string }>("/videos/inspire-me", {
          user_idea: prompt,
          engine,
          mode,
          duration,
          aspect_ratio: aspectRatio,
          camera_preset: engine === "kling" ? cameraPreset : null,
          reference_image_url: mode === "image_to_video" ? referenceImageUrl : null,
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      setPrompt(data.enhanced_prompt);
    },
    onError: (e: Error) => {
      toast({
        title: "Inspire Me failed",
        description: e.message,
        variant: "destructive",
      });
    },
  });

  const [uploadingRef, setUploadingRef] = useState(false);

  const handleReferenceUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Show local preview immediately
    const previewUrl = URL.createObjectURL(file);
    setReferenceImageUrl(previewUrl);
    if (fileRef.current) fileRef.current.value = "";

    // Upload to R2 so fal.ai can access it
    setUploadingRef(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { data } = await api.post("/videos/upload-reference", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      // Replace blob URL with the real R2 URL
      setReferenceImageUrl(data.url);
    } catch (err) {
      console.error("Reference image upload failed:", err);
      // Keep the blob preview but warn the user
    } finally {
      setUploadingRef(false);
    }
  };

  const handleClose = () => {
    setResult(null);
    onClose();
  };

  const handleGenerateMore = () => {
    setResult(null);
  };

  const canGenerate =
    (mode === "text_to_video" || (referenceImageUrl && !referenceImageUrl.startsWith("blob:"))) &&
    !generateMutation.isPending &&
    !uploadingRef;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-accent" />
            Generate Video with AI
          </DialogTitle>
        </DialogHeader>

        {/* Success state */}
        {result ? (
          <div className="space-y-4">
            <div
              className={cn(
                "grid gap-2",
                result.videos.length === 1
                  ? "grid-cols-1"
                  : "grid-cols-2"
              )}
            >
              {result.videos.map((v) => (
                <video
                  key={v.id}
                  src={v.url}
                  className="w-full rounded-lg"
                  controls
                  preload="metadata"
                />
              ))}
            </div>
            <p className="text-xs text-text-dim">
              Cost: ~${result.total_cost_usd.toFixed(2)}
            </p>
            <div className="flex gap-2">
              {onViewInLibrary && (
                <Button
                  size="sm"
                  onClick={() => {
                    onViewInLibrary();
                    handleClose();
                  }}
                >
                  View in library
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={handleGenerateMore}
              >
                Generate more
              </Button>
              <Button size="sm" variant="outline" onClick={handleClose}>
                Close
              </Button>
            </div>
          </div>
        ) : (
          /* Input state */
          <div className="space-y-4">
            {/* Engine selector */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Engine
              </label>
              <div className="flex gap-1">
                {(["kling", "wan"] as Engine[]).map((e) => (
                  <button
                    key={e}
                    onClick={() => {
                      setEngine(e);
                      if (e === "wan") setCameraPreset("none");
                    }}
                    className={cn(
                      "rounded-md px-4 py-1.5 text-xs font-medium transition-colors capitalize",
                      engine === e
                        ? "bg-accent text-white"
                        : "bg-surface-2 text-text-dim hover:text-text"
                    )}
                  >
                    {e === "kling" ? "Kling" : "Wan"}
                  </button>
                ))}
              </div>
            </div>

            {/* Mode selector */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Mode
              </label>
              <div className="flex gap-1">
                <button
                  onClick={() => setMode("text_to_video")}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                    mode === "text_to_video"
                      ? "bg-accent text-white"
                      : "bg-surface-2 text-text-dim hover:text-text"
                  )}
                >
                  Text to Video
                </button>
                <button
                  onClick={() => setMode("image_to_video")}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                    mode === "image_to_video"
                      ? "bg-accent text-white"
                      : "bg-surface-2 text-text-dim hover:text-text"
                  )}
                >
                  Image to Video
                </button>
              </div>
            </div>

            {/* Reference image picker (I2V only) */}
            {mode === "image_to_video" && (
              <div>
                <label className="mb-1 block text-xs font-medium text-text-dim">
                  Reference Image
                </label>
                {referenceImageUrl ? (
                  <div className="relative inline-block">
                    <img
                      src={referenceImageUrl}
                      alt="Reference"
                      className="h-24 w-24 rounded-lg object-cover border border-border"
                    />
                    {uploadingRef && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/50 rounded-lg">
                        <span className="text-[10px] text-white">Uploading...</span>
                      </div>
                    )}
                    <button
                      onClick={() => setReferenceImageUrl(null)}
                      className="absolute -right-1 -top-1 rounded-full bg-red-600 p-0.5 text-white"
                    >
                      <span className="text-xs leading-none">&times;</span>
                    </button>
                  </div>
                ) : (
                  <div>
                    <input
                      ref={fileRef}
                      type="file"
                      accept="image/*"
                      className="hidden"
                      onChange={handleReferenceUpload}
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => fileRef.current?.click()}
                    >
                      <Upload className="mr-1.5 h-3.5 w-3.5" />
                      Upload image
                    </Button>
                  </div>
                )}
              </div>
            )}

            {/* Prompt */}
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-text-dim">
                  Prompt
                </label>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => inspireMutation.mutate()}
                  disabled={inspireMutation.isPending}
                  className="h-6 px-2 text-xs"
                >
                  {inspireMutation.isPending ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <Sparkles className="mr-1 h-3 w-3" />
                  )}
                  Inspire Me
                </Button>
              </div>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                maxLength={2000}
                rows={3}
                placeholder="Describe the video you want to generate..."
                className="w-full rounded-md border border-border bg-surface p-2.5 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-hidden"
              />
              <span className="text-xs text-text-dim">
                {2000 - prompt.length} chars remaining
              </span>
            </div>

            {/* Duration */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Duration
              </label>
              <div className="flex gap-1">
                {DURATIONS.map((d) => (
                  <button
                    key={d}
                    onClick={() => setDuration(d)}
                    className={cn(
                      "rounded-md px-4 py-1.5 text-xs font-medium transition-colors",
                      duration === d
                        ? "bg-accent text-white"
                        : "bg-surface-2 text-text-dim hover:text-text"
                    )}
                  >
                    {d}s
                  </button>
                ))}
              </div>
            </div>

            {/* Aspect ratio */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Aspect ratio
              </label>
              <div className="flex gap-1">
                {ASPECT_RATIOS.map((ar) => (
                  <button
                    key={ar}
                    onClick={() => setAspectRatio(ar)}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                      aspectRatio === ar
                        ? "bg-accent text-white"
                        : "bg-surface-2 text-text-dim hover:text-text"
                    )}
                  >
                    {ar}
                  </button>
                ))}
              </div>
            </div>

            {/* Camera preset (Kling only) */}
            {engine === "kling" && (
              <div>
                <label className="mb-1 block text-xs font-medium text-text-dim">
                  Camera Motion
                </label>
                <div className="flex flex-wrap gap-1">
                  {KLING_CAMERA_PRESETS.map((cp) => (
                    <button
                      key={cp.value}
                      onClick={() => setCameraPreset(cp.value)}
                      className={cn(
                        "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                        cameraPreset === cp.value
                          ? "bg-accent text-white"
                          : "bg-surface-2 text-text-dim hover:text-text"
                      )}
                    >
                      {cp.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Variations */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Variations
              </label>
              <div className="flex gap-1">
                {NUM_OPTIONS.map((n) => (
                  <button
                    key={n}
                    onClick={() => setNumVideos(n)}
                    className={cn(
                      "rounded-md px-4 py-1.5 text-xs font-medium transition-colors",
                      numVideos === n
                        ? "bg-accent text-white"
                        : "bg-surface-2 text-text-dim hover:text-text"
                    )}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>

            {/* Advanced toggle */}
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-1 text-xs text-text-dim hover:text-text"
            >
              {showAdvanced ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
              Advanced options
            </button>

            {showAdvanced && (
              <div className="space-y-3 rounded-md border border-border p-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-text-dim">
                    Negative prompt
                  </label>
                  <textarea
                    value={negativePrompt}
                    onChange={(e) => setNegativePrompt(e.target.value)}
                    rows={2}
                    placeholder="Things to avoid in the generated video..."
                    className="w-full rounded-md border border-border bg-surface p-2.5 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-hidden"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs font-medium text-text-dim">
                    Seed
                  </label>
                  <input
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(e.target.value)}
                    placeholder="Random"
                    className="w-32 rounded-md border border-border bg-surface p-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-hidden"
                  />
                  <button
                    onClick={() =>
                      setSeed(
                        Math.floor(Math.random() * 2147483647).toString()
                      )
                    }
                    className="rounded-md p-1 text-text-dim hover:text-text"
                    title="Random seed"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}

            {/* Cost estimate + Generate */}
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs text-text-dim">
                ~${costEstimate(engine, duration, numVideos)} for {numVideos} video{numVideos > 1 ? "s" : ""} at {duration}s
              </span>
              <Button
                onClick={() => generateMutation.mutate()}
                disabled={!canGenerate}
                title={!canGenerate ? (prompt.trim().length === 0 ? "Enter a prompt or click Inspire Me" : uploadingRef ? "Uploading reference image..." : "Select a reference image") : ""}
              >
                {generateMutation.isPending ? (
                  <>
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    Generating...
                  </>
                ) : (
                  <>
                    <Play className="mr-1.5 h-4 w-4" />
                    Generate {numVideos > 1 ? `${numVideos} videos` : "video"}
                  </>
                )}
              </Button>
            </div>

            {/* Progress state */}
            {generateMutation.isPending && (
              <div className="rounded-md border border-border bg-surface p-4 text-center">
                <Loader2 className="mx-auto mb-2 h-8 w-8 animate-spin text-accent" />
                <p className="text-sm text-text">
                  Generating {numVideos} video
                  {numVideos > 1 ? "s" : ""}...
                </p>
                <p className="mt-1 text-xs text-text-dim">
                  ~{timeEstimate(engine, duration, numVideos)} seconds estimated
                </p>
              </div>
            )}

            {/* Error state */}
            {generateMutation.isError && (
              <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3">
                <p className="text-sm text-red-400">
                  {(generateMutation.error as Error)?.message ||
                    "Generation failed"}
                </p>
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => generateMutation.mutate()}
                  >
                    Retry
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleClose}
                  >
                    Close
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
