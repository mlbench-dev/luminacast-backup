import { useState } from "react";
import { Dna, Loader2, Plus, Search, X, RefreshCw, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { avatarApi } from "@/lib/api";
import type { Avatar, StyleDNAResult } from "@/lib/types";
import { toast } from "@/hooks/useToast";

interface StyleDNAProps {
  avatar: Avatar;
  onAnalyzed?: () => void;
}

export function StyleDNA({ avatar, onAnalyzed }: StyleDNAProps) {
  const initial = (avatar.style_dna ?? null) as StyleDNAResult | null;
  const [urls, setUrls] = useState<string[]>([""]);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<StyleDNAResult | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const [confirmReplace, setConfirmReplace] = useState(false);

  const hasExistingVoice = !!avatar.voice_id;

  const runAnalysis = async () => {
    const cleanUrls = urls.map((u) => u.trim()).filter(Boolean).slice(0, 3);
    if (cleanUrls.length === 0) return;
    setError(null);
    setAnalyzing(true);
    try {
      const data = await avatarApi.analyzeStyle(avatar.id, cleanUrls);
      setResult(data.style_dna);
      const dropped = data.requested_videos - data.successful_videos;
      const detail = dropped > 0
        ? `${data.successful_videos} of ${data.requested_videos} videos analyzed.`
        : "All videos analyzed successfully.";
      toast({ title: "Style DNA captured", description: detail });
      onAnalyzed?.();
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "AI analysis is currently unavailable. Try again in a moment.";
      setError(msg);
    } finally {
      setAnalyzing(false);
      setConfirmReplace(false);
    }
  };

  const handleAnalyzeClick = () => {
    if (hasExistingVoice && !confirmReplace) {
      setConfirmReplace(true);
      return;
    }
    void runAnalysis();
  };

  const handleReset = async () => {
    try {
      await avatarApi.resetStyleDNA(avatar.id);
      setResult(null);
      setUrls([""]);
      setError(null);
      onAnalyzed?.();
    } catch {
      toast({
        title: "Couldn't reset",
        description: "Try again or refresh the page.",
        variant: "destructive",
      });
    }
  };

  return (
    <div className="mt-8 p-5 bg-white/[0.03] border border-white/10 rounded-xl">
      <div className="flex items-center gap-2 mb-4">
        <Dna className="w-4 h-4 text-accent" />
        <h3 className="text-sm font-medium">Style DNA</h3>
        <span className="text-[10px] text-white/30 ml-auto">
          Clone a creator's editing style from their videos
        </span>
      </div>

      {result ? (
        <StyleDNAResultPanel result={result} onReset={handleReset} />
      ) : (
        <div>
          <p className="text-xs text-white/40 mb-3">
            Paste 1-3 video URLs. We'll analyze their voice, pacing, hook patterns,
            caption style, and editing rhythm.
          </p>

          {urls.map((url, i) => (
            <div key={i} className="flex gap-2 mb-2">
              <input
                value={url}
                onChange={(e) => {
                  const next = [...urls];
                  next[i] = e.target.value;
                  setUrls(next);
                }}
                placeholder={"https://www.tiktok.com/@creator/video/..."}
                className="flex-1 text-xs bg-white/5 border border-white/10 rounded-lg px-3 py-2 outline-none focus:border-accent/50"
              />
              {urls.length > 1 && (
                <button
                  onClick={() => setUrls(urls.filter((_, j) => j !== i))}
                  className="text-white/20 hover:text-red-400"
                  aria-label="Remove video"
                  type="button"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          ))}

          {urls.length < 3 && (
            <button
              onClick={() => setUrls([...urls, ""])}
              type="button"
              className="text-[10px] text-accent hover:text-accent/80 mb-3 flex items-center gap-1"
            >
              <Plus className="w-3 h-3" /> Add another video
            </button>
          )}

          {confirmReplace && hasExistingVoice && (
            <div className="mb-3 p-3 rounded-lg border border-amber-400/40 bg-amber-400/5 text-xs text-amber-200">
              Style DNA will replace your existing voice clone.
              <div className="mt-2 flex gap-2">
                <Button size="sm" onClick={runAnalysis} disabled={analyzing}>
                  Continue
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setConfirmReplace(false)}
                  disabled={analyzing}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {!confirmReplace && (
            <Button
              onClick={handleAnalyzeClick}
              disabled={analyzing || !urls.some((u) => u.trim())}
            >
              {analyzing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Analyzing...
                </>
              ) : (
                <>
                  <Search className="w-3.5 h-3.5 mr-1.5" /> Analyze Videos
                </>
              )}
            </Button>
          )}

          {error && (
            <div className="mt-3 flex items-start gap-2 text-[11px] text-red-300">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <div>
                {error}
                <button
                  type="button"
                  onClick={() => setError(null)}
                  className="ml-2 underline hover:text-red-200"
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}

          <p className="text-[10px] text-white/15 mt-2">
            Supports TikTok, Instagram Reels, YouTube Shorts. ~$0.20 per analysis (3 videos).
          </p>
        </div>
      )}
    </div>
  );
}

function StyleDNAResultPanel({
  result,
  onReset,
}: {
  result: StyleDNAResult;
  onReset: () => void;
}) {
  const cutEvery =
    typeof result.cut_frequency_seconds === "number"
      ? `Every ${result.cut_frequency_seconds}s`
      : "—";
  const brollPct =
    typeof result.broll_ratio === "number"
      ? `${Math.round(result.broll_ratio * 100)}%`
      : "—";
  const energy =
    typeof result.avg_energy === "number" ? `${result.avg_energy}/10` : "—";
  const voiceLine =
    typeof result.voice_duration_s === "number" && result.voice_duration_s > 0
      ? `${result.voice_duration_s}s extracted`
      : "Voice clone unavailable";

  return (
    <div>
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="p-3 bg-white/[0.03] rounded-lg">
          <div className="text-[10px] text-white/30 mb-1">Voice sample</div>
          <div className="text-sm text-emerald-400">{voiceLine}</div>
        </div>
        <div className="p-3 bg-white/[0.03] rounded-lg">
          <div className="text-[10px] text-white/30 mb-1">Energy</div>
          <div className="text-sm">{energy}</div>
        </div>
        <div className="p-3 bg-white/[0.03] rounded-lg">
          <div className="text-[10px] text-white/30 mb-1">Cut frequency</div>
          <div className="text-sm">{cutEvery}</div>
        </div>
        <div className="p-3 bg-white/[0.03] rounded-lg">
          <div className="text-[10px] text-white/30 mb-1">B-roll ratio</div>
          <div className="text-sm">{brollPct}</div>
        </div>
      </div>

      {result.tone && (
        <div className="mb-3">
          <div className="text-[10px] text-white/30 mb-1">Speaking style</div>
          <div className="text-xs text-white/60">{result.tone}</div>
        </div>
      )}

      {result.common_phrases && result.common_phrases.length > 0 && (
        <div className="mb-3">
          <div className="text-[10px] text-white/30 mb-1">Signature phrases</div>
          <div className="flex flex-wrap gap-1.5">
            {result.common_phrases.slice(0, 8).map((p, i) => (
              <span
                key={i}
                className="text-[10px] px-2 py-0.5 bg-white/5 border border-white/10 rounded-full text-white/50"
              >
                "{p}"
              </span>
            ))}
          </div>
        </div>
      )}

      {result.caption_preset && (
        <div className="mb-3">
          <div className="text-[10px] text-white/30 mb-1">Caption preset match</div>
          <div className="text-xs text-white/60">{result.caption_preset}</div>
        </div>
      )}

      <div className="flex gap-2 mt-4">
        <Button size="sm" variant="outline" onClick={onReset}>
          <RefreshCw className="w-3 h-3 mr-1" /> Analyze different videos
        </Button>
      </div>
    </div>
  );
}
