import { useState } from "react";
import { Mic } from "lucide-react";
import { avatarApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";

interface ClipMicToggleProps {
  avatarId: string;
  initialEnabled: boolean;
  /** Optional callback fired after the PATCH succeeds. The cast script
   * page uses this to nudge the user to regenerate TTS so the new mic
   * profile applies immediately. */
  onChange?: (enabled: boolean) => void;
  className?: string;
}

/**
 * Lavalier-vs-phone-mic toggle, persisted per-avatar via
 * PATCH /api/avatar/{id} { clip_mic_enabled }.
 *
 * Default OFF = phone mic — natural TikTok/vlog feel.
 * ON = clip-on lavalier — warm proximity, podcast-intimate.
 *
 * The toggle drives two layers in the backend:
 *   1. Voice description: a mic-style suffix is appended to the
 *      voice_description sent to the cloning engine.
 *   2. Post-process EQ: the TTS post-processor switches between two
 *      compand / equalizer profiles before writing the master MP3 +
 *      lipsync WAV.
 */
export function ClipMicToggle({
  avatarId,
  initialEnabled,
  onChange,
  className,
}: ClipMicToggleProps) {
  const [enabled, setEnabled] = useState<boolean>(!!initialEnabled);
  const [saving, setSaving] = useState(false);

  const handleChange = async (next: boolean) => {
    if (!avatarId) return;
    const previous = enabled;
    setEnabled(next);
    setSaving(true);
    try {
      await avatarApi.updateAvatar(avatarId, { clip_mic_enabled: next });
      onChange?.(next);
    } catch (err: any) {
      setEnabled(previous);
      toast({
        title: "Couldn't update mic style",
        description: err?.response?.data?.detail || err?.message || "",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const description = enabled
    ? "Lavalier mic — close, warm, intimate. Podcast/interview feel."
    : "Phone mic — natural, room tone, slightly distant. TikTok/vlog feel.";

  return (
    <div
      className={cn(
        "flex items-center justify-between mt-4 p-3 bg-white/[0.03] border border-white/10 rounded-lg",
        className,
      )}
      data-testid="clip-mic-toggle"
    >
      <div className="flex items-center gap-3 min-w-0">
        <Mic className="w-4 h-4 text-white/40 shrink-0" />
        <div className="min-w-0">
          <div className="text-sm font-medium">Clip mic</div>
          <p className="text-[10px] text-white/30 truncate">{description}</p>
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label="Toggle clip mic"
        disabled={saving}
        onClick={() => handleChange(!enabled)}
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
          enabled ? "bg-purple-500" : "bg-white/10",
          saving && "opacity-50",
        )}
        data-testid="clip-mic-toggle-switch"
      >
        <span
          className={cn(
            "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
            enabled ? "translate-x-4" : "translate-x-1",
          )}
        />
      </button>
    </div>
  );
}
