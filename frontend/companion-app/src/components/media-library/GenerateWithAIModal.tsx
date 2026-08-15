import { useState } from "react";
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
} from "lucide-react";

interface GenerateWithAIModalProps {
  open: boolean;
  onClose: () => void;
  initialPrompt?: string;
  initialNegativePrompt?: string;
  onViewInLibrary?: () => void;
}

const ASPECT_RATIOS = [
  { label: "1:1", width: 1024, height: 1024 },
  { label: "9:16", width: 768, height: 1344 },
  { label: "16:9", width: 1344, height: 768 },
  { label: "4:5", width: 896, height: 1152 },
  { label: "3:4", width: 896, height: 1216 },
] as const;

const NUM_OPTIONS = [1, 2, 4] as const;

interface GeneratedResult {
  photos: Array<{
    id: string;
    url: string;
    prompt: string;
    width: number;
    height: number;
    engine_used: string;
    created_at: string;
  }>;
  engine_used: string;
  tier: number;
}

export function GenerateWithAIModal({
  open,
  onClose,
  initialPrompt = "",
  initialNegativePrompt = "",
  onViewInLibrary,
}: GenerateWithAIModalProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [prompt, setPrompt] = useState(initialPrompt);
  const [negativePrompt, setNegativePrompt] = useState(
    initialNegativePrompt
  );
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [seed, setSeed] = useState<string>("");
  const [aspectIdx, setAspectIdx] = useState(0);
  const [numImages, setNumImages] = useState<1 | 2 | 4>(1);
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
    mutationFn: () =>
      api
        .post<GeneratedResult>("/photos/generate", {
          prompt,
          negative_prompt: negativePrompt || null,
          width: ASPECT_RATIOS[aspectIdx].width,
          height: ASPECT_RATIOS[aspectIdx].height,
          num_images: numImages,
          seed: seed ? parseInt(seed, 10) : null,
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ["generated-photos"] });
    },
    onError: (e: Error) => {
      toast({
        title: "Generation failed",
        description: e.message,
        variant: "destructive",
      });
    },
  });

  const estimateCost = () => {
    const ar = ASPECT_RATIOS[aspectIdx];
    const pixelCost = (ar.width * ar.height) / (1024 * 1024);
    const perImage = pixelCost * 5.0;
    const totalSec = perImage * numImages;
    const cost = (totalSec / 3600) * 0.5;
    return Math.max(cost, 0.003 * numImages).toFixed(3);
  };

  const handleClose = () => {
    setResult(null);
    onClose();
  };

  const handleGenerateMore = () => {
    setResult(null);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-accent" />
            Generate with AI
          </DialogTitle>
        </DialogHeader>

        {/* Success state */}
        {result ? (
          <div className="space-y-4">
            <div
              className={cn(
                "grid gap-2",
                result.photos.length === 1
                  ? "grid-cols-1"
                  : "grid-cols-2"
              )}
            >
              {result.photos.map((p) => (
                <img
                  key={p.id}
                  src={p.url}
                  alt={p.prompt}
                  className="w-full rounded-lg"
                />
              ))}
            </div>
            <p className="text-xs text-text-dim">
              Generated via Tier {result.tier}
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
            {/* Prompt */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Prompt
              </label>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                maxLength={2000}
                rows={3}
                placeholder="Describe the image you want to generate..."
                className="w-full rounded-md border border-border bg-surface p-2.5 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-hidden"
              />
              <span className="text-xs text-text-dim">
                {prompt.length}/2000
              </span>
            </div>

            {/* Aspect ratio */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Aspect ratio
              </label>
              <div className="flex gap-1">
                {ASPECT_RATIOS.map((ar, idx) => (
                  <button
                    key={ar.label}
                    onClick={() => setAspectIdx(idx)}
                    className={cn(
                      "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                      aspectIdx === idx
                        ? "bg-accent text-white"
                        : "bg-surface-2 text-text-dim hover:text-text"
                    )}
                  >
                    {ar.label}
                  </button>
                ))}
              </div>
              <span className="text-xs text-text-muted">
                {ASPECT_RATIOS[aspectIdx].width}&times;
                {ASPECT_RATIOS[aspectIdx].height}
              </span>
            </div>

            {/* Number of images */}
            <div>
              <label className="mb-1 block text-xs font-medium text-text-dim">
                Number of images
              </label>
              <div className="flex gap-1">
                {NUM_OPTIONS.map((n) => (
                  <button
                    key={n}
                    onClick={() => setNumImages(n)}
                    className={cn(
                      "rounded-md px-4 py-1.5 text-xs font-medium transition-colors",
                      numImages === n
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
                    placeholder="Things to avoid in the generated image..."
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
                Approximately ${estimateCost()}
              </span>
              <Button
                onClick={() => generateMutation.mutate()}
                disabled={
                  !prompt.trim() || generateMutation.isPending
                }
              >
                {generateMutation.isPending ? (
                  <>
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    Generating...
                  </>
                ) : (
                  <>
                    <Sparkles className="mr-1.5 h-4 w-4" />
                    Generate
                  </>
                )}
              </Button>
            </div>

            {/* Progress state */}
            {generateMutation.isPending && (
              <div className="rounded-md border border-border bg-surface p-4 text-center">
                <Loader2 className="mx-auto mb-2 h-8 w-8 animate-spin text-accent" />
                <p className="text-sm text-text">
                  Generating {numImages} image
                  {numImages > 1 ? "s" : ""}...
                </p>
                <p className="mt-1 text-xs text-text-dim">
                  This may take 15-60 seconds
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
