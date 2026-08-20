import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ChevronDown, Loader2, Sparkles, Plus, X } from "lucide-react";
import { avatarLooksApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import type { AvatarLook } from "@/lib/types";

/**
 * AvatarLookPicker \u2014 a single-select dropdown over the avatar's existing
 * background looks PLUS an inline "+ Generate new" form. Used in two
 * places:
 *
 *   1. SetupPhase \u2014 pick the cast-wide default background.
 *   2. ScriptPhase \u2014 per-block override (replaces the legacy raw <select>).
 *
 * Why a dedicated component: the user wants the same generate-flow in
 * both places, and inlining the form twice is a recipe for divergence.
 *
 * The component is unopinionated about persistence \u2014 the parent passes
 * `value` + `onChange(lookId)`; how that gets saved (PATCH cast,
 * updateBlock, etc.) is the parent's call.
 */

interface Props {
  avatarId: string;
  /** Available looks (already filtered to background look_type + ready). */
  looks: AvatarLook[];
  value: string | null | undefined;
  onChange: (lookId: string | null) => void;
  /** Optional fallback label for the "no look picked" entry. */
  defaultLabel?: string;
  /** When a new look's generation kicks off, refetch the looks list. */
  onLookCreated?: () => void;
  /** Compact size for inline use in script blocks. */
  size?: "sm" | "md";
  /** Optional class. */
  className?: string;
}

export function AvatarLookPicker({
  avatarId,
  looks,
  value,
  onChange,
  defaultLabel = "Avatar default",
  onLookCreated,
  size = "sm",
  className,
}: Props) {
  const [genOpen, setGenOpen] = useState(false);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      avatarLooksApi.create(avatarId, {
        name: name.trim() || prompt.trim().slice(0, 60) || "Custom scene",
        background_prompt: prompt.trim() || undefined,
        look_type: "background",
      }),
    onSuccess: (look: AvatarLook) => {
      toast({
        title: "Scene generation started",
        description:
          "It usually takes 30\u201360 seconds. We'll list it the moment it's ready.",
        variant: "success",
      });
      // Optimistically pre-select the in-flight look so it shows up the
      // second its status flips to ready.
      if (look?.id) onChange(look.id);
      setName("");
      setPrompt("");
      setGenOpen(false);
      onLookCreated?.();
    },
    onError: (err: any) => {
      toast({
        title: "Could not start generation",
        description:
          err?.response?.data?.detail ||
          err?.message ||
          "Try again in a moment.",
        variant: "destructive",
      });
    },
  });

  const selected = looks.find((l) => l.id === value);

  return (
    <div className={cn("space-y-1.5", className)}>
      {/* Selector row \u2014 thumbnails of existing looks + a small Generate
          button. Showing actual thumbnails instead of a bare <select>
          gives the user immediate visual feedback on what they're
          picking. */}
      <div className="flex flex-wrap items-center gap-1.5">
        {/* Default / no-override option */}
        <LookChip
          label={defaultLabel}
          thumbUrl={null}
          active={!value}
          onClick={() => onChange(null)}
          size={size}
        />
        {looks.map((look) => (
          <LookChip
            key={look.id}
            label={look.name}
            thumbUrl={
              look.image_url ||
              (look.face_ref_key ? cdnUrl(look.face_ref_key) : null)
            }
            active={value === look.id}
            onClick={() => onChange(look.id)}
            size={size}
          />
        ))}
        <button
          type="button"
          onClick={() => setGenOpen((v) => !v)}
          className={cn(
            "inline-flex items-center gap-1 rounded-md border border-dashed border-white/15 px-2 py-1 text-[10px] font-medium text-white/55 transition-colors",
            "hover:border-accent/40 hover:bg-accent/5 hover:text-accent",
            size === "md" && "px-2.5 py-1.5 text-xs",
          )}
          title="Generate a new scene"
        >
          {genOpen ? (
            <>
              <X className="w-3 h-3" /> Close
            </>
          ) : (
            <>
              <Plus className="w-3 h-3" /> Generate new
            </>
          )}
        </button>
      </div>

      {/* Selected look name (small) \u2014 helpful when the chip strip is
          truncated. */}
      {selected && size === "md" && (
        <div className="text-[10px] text-white/40">
          Picked: <span className="text-white/70">{selected.name}</span>
        </div>
      )}

      {/* Inline generate form */}
      {genOpen && (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-2.5 space-y-2">
          <input
            type="text"
            placeholder="Name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value.slice(0, 80))}
            className="w-full bg-black/20 border border-white/10 rounded px-2 py-1 text-[11px] text-white/85 placeholder:text-white/30 focus:outline-none focus:border-accent/40"
          />
          <textarea
            placeholder="Describe the scene (e.g. 'soft pastel bedroom morning light, beauty creator vibe')"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value.slice(0, 800))}
            rows={2}
            className="w-full bg-black/20 border border-white/10 rounded px-2 py-1 text-[11px] text-white/85 placeholder:text-white/30 focus:outline-none focus:border-accent/40 resize-none"
          />
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-white/30">
              The avatar's face stays the same; only the scene changes.
            </span>
            <button
              type="button"
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !prompt.trim()}
              className={cn(
                "inline-flex items-center gap-1 rounded-md bg-accent/15 hover:bg-accent/25 text-accent px-2.5 py-1 text-[11px] font-medium transition",
                "disabled:opacity-40 disabled:cursor-not-allowed",
              )}
            >
              {createMutation.isPending ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Sparkles className="w-3 h-3" />
              )}
              Generate
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function LookChip({
  label,
  thumbUrl,
  active,
  onClick,
  size,
}: {
  label: string;
  thumbUrl: string | null;
  active: boolean;
  onClick: () => void;
  size: "sm" | "md";
}) {
  const dim = size === "md" ? "h-52 w-52" : "h-36 w-36";

  // No thumbnail (the "use default / no override" option) shouldn't claim a
  // full square photo-frame's worth of space just to show one small icon
  // floating in an otherwise-empty box — that reads as broken, especially
  // when it's the only chip in the row (no looks generated yet for this
  // avatar). Render it as a compact labeled pill instead; real thumbnails
  // keep the square treatment below.
  if (!thumbUrl) {
    return (
      <button
        type="button"
        onClick={onClick}
        title={label}
        className={cn(
          "shrink-0 inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[11px] font-medium transition-colors",
          active
            ? "border-accent/60 bg-accent/15 text-white"
            : "border-white/10 bg-white/[0.03] text-white/55 hover:border-white/25 hover:bg-white/[0.06] hover:text-white/80",
          size === "md" && "px-3 py-2 text-xs",
        )}
      >
        <ChevronDown className="w-3 h-3 rotate-90 shrink-0" />
        <span className="truncate max-w-[140px]">{label}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      className={cn(
        "group relative shrink-0 rounded-md overflow-hidden border transition-all",
        active
          ? "border-accent ring-2 ring-accent/30"
          : "border-white/10 hover:border-white/30",
        dim,
      )}
    >
      <img src={thumbUrl} alt="" className="w-full h-full object-cover" />
      {/* Hover label tooltip */}
      <span
        className={cn(
          "pointer-events-none absolute -bottom-0.5 left-1/2 -translate-x-1/2 translate-y-full",
          "rounded bg-black/85 ring-1 ring-white/10 px-1.5 py-0.5 text-[9px] text-white/80 whitespace-nowrap",
          "opacity-0 group-hover:opacity-100 transition-opacity z-10",
          "max-w-[140px] truncate",
        )}
      >
        {label}
      </span>
    </button>
  );
}
