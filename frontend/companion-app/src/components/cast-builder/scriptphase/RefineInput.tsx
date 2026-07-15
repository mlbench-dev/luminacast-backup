import { useState } from "react";
import { Sparkles, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { scriptApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";

/**
 * RefineInput — single-line natural-language refine bar that lives at
 * the bottom of every block card. The user types "make it more urgent"
 * or "add a stat about retention", hits Enter, and the active variant's
 * script gets rewritten in place by the LLM (via the existing
 * /casts/.../variants/.../rewrite endpoint).
 *
 * The Regenerate button next to it triggers a from-scratch rewrite
 * using the same endpoint with a generic prompt — useful when the user
 * doesn't have a specific instruction but wants a fresh take.
 *
 * UX details:
 *   - Enter submits, Shift+Enter does nothing special (we keep it
 *     single-line; for multi-line edits the textarea above is the spot).
 *   - Disabled while a request is in flight; loading indicator on the
 *     pressed button only so the other stays clickable for cancel-style
 *     UX (we don't actually support cancel, but disabling both feels
 *     overly aggressive).
 *   - Quick-suggestion chips below — common refines users hit constantly.
 */

interface Props {
  castId: string;
  blockId: string;
  variantId: string | null;
  /** Existing script text, used as the regenerate baseline. */
  currentText: string;
  /** Called with the new script text after a successful rewrite. */
  onRewritten: (newText: string) => void;
  /** Hide the quick-suggestion chips (they double card height). */
  compact?: boolean;
}

const SUGGESTIONS = [
  "Make it more urgent",
  "Add humor",
  "Cut to 20 words",
  "More casual",
  "Stronger CTA",
];

export function RefineInput({
  castId,
  blockId,
  variantId,
  currentText,
  onRewritten,
  compact,
}: Props) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState<"refine" | "regen" | null>(null);

  const callRewrite = async (prompt: string, kind: "refine" | "regen") => {
    if (!variantId) {
      toast({
        title: "No script yet",
        description: "Wait for the script to load before refining.",
        variant: "destructive",
      });
      return;
    }
    if (!prompt.trim()) return;
    setBusy(kind);
    try {
      const res = await scriptApi.rewrite(castId, blockId, variantId, prompt);
      onRewritten(res.script_text);
      if (kind === "refine") setInput("");
      toast({
        title: kind === "refine" ? "Refined" : "Regenerated",
        description: prompt.length > 60 ? prompt.slice(0, 57) + "…" : prompt,
        variant: "success",
      });
    } catch (e: any) {
      toast({
        title: "Refine failed",
        description: e?.response?.data?.detail || e?.message || "Please try again.",
        variant: "destructive",
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Sparkles className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-accent/70 pointer-events-none" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.stopPropagation();
                callRewrite(input, "refine");
              }
            }}
            placeholder="Refine with AI: e.g. 'make it more urgent', 'add humor'"
            disabled={busy !== null}
            className={cn(
              "w-full bg-white/[0.04] border border-white/10 rounded-lg pl-8 pr-3 py-2 text-xs text-white/85 placeholder:text-white/30",
              "focus:outline-none focus:border-accent/40 focus:bg-white/[0.06]",
              "disabled:opacity-50",
            )}
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => callRewrite(input, "refine")}
          disabled={busy !== null || !input.trim()}
          className="border-accent/30 text-accent hover:bg-accent/10 hover:border-accent/50 h-8"
          title="Apply this instruction to the script"
        >
          {busy === "refine" ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Sparkles className="w-3 h-3" />
          )}
          <span className="ml-1.5">Refine</span>
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            callRewrite(
              "Rewrite this from scratch — keep the same key points but use fresh wording, vary the hook, and keep it under 40 words.",
              "regen",
            )
          }
          disabled={busy !== null || !currentText.trim()}
          className="border-white/15 text-white/70 hover:bg-white/5 h-8"
          title="Generate a fresh take of this block"
        >
          {busy === "regen" ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <RefreshCw className="w-3 h-3" />
          )}
          <span className="ml-1.5">Regenerate</span>
        </Button>
      </div>
      {!compact && (
        <div className="flex flex-wrap gap-1">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => callRewrite(s, "refine")}
              disabled={busy !== null}
              className={cn(
                "text-[10px] px-2 py-0.5 rounded-full border border-white/10 bg-white/[0.02] text-white/45",
                "hover:border-accent/30 hover:text-accent/90 hover:bg-accent/5 transition-colors",
                "disabled:opacity-40 disabled:cursor-not-allowed",
              )}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
