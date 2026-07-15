import { useState } from "react";
import {
  Smile,
  PartyPopper,
  Subtitles,
  PictureInPicture2,
  Stamp,
  ChevronDown,
  ChevronUp,
  Palette,
  Crop,
  Info,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { BackgroundPicker } from "@/components/BackgroundPicker";
import type { EffectsConfig } from "@/lib/types";

const EMOJI_PRESETS = ["❤️", "🔥", "😍", "🎉", "💯", "👏", "🤩", "💰", "🛒", "⭐"];

interface EffectsPanelProps {
  effectsConfig: EffectsConfig;
  onChange: (config: EffectsConfig) => void;
  onBackgroundUpload?: (file: File, mediaType: "image" | "video") => void;
}

function EffectToggle({
  icon: Icon,
  label,
  description,
  enabled,
  disabled,
  onToggle,
  children,
}: {
  icon: React.ElementType;
  label: string;
  description: string;
  enabled: boolean;
  disabled?: boolean;
  onToggle: () => void;
  children?: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={cn(
      "rounded-lg border transition-colors",
      enabled ? "border-accent/40 bg-accent/5" : "border-border bg-surface",
      disabled && "opacity-50",
    )}>
      <div className="flex items-center gap-3 p-3">
        <Icon className={cn("h-4 w-4 shrink-0", enabled ? "text-accent" : "text-text-muted")} />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-text">{label}</p>
          <p className="text-[10px] text-text-muted">{description}</p>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={disabled ? undefined : onToggle}
            className={cn(
              "relative h-5 w-9 rounded-full transition-colors",
              enabled ? "bg-accent" : "bg-border",
              disabled && "cursor-not-allowed",
            )}
          >
            <div className={cn(
              "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
              enabled ? "translate-x-4" : "translate-x-0.5",
            )} />
          </button>
          {children && enabled && (
            <button onClick={() => setExpanded(!expanded)} className="p-0.5">
              {expanded ? <ChevronUp className="h-3 w-3 text-text-muted" /> : <ChevronDown className="h-3 w-3 text-text-muted" />}
            </button>
          )}
        </div>
      </div>
      {children && enabled && expanded && (
        <div className="border-t border-border/50 px-3 pb-3 pt-2 space-y-2">
          {children}
        </div>
      )}
    </div>
  );
}

function SelectRow({ label, value, options, onChange }: {
  label: string;
  value: string | number;
  options: { value: string | number; label: string }[];
  onChange: (val: string) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[10px] text-text-dim">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-6 rounded border border-border bg-bg px-1.5 text-[10px] text-text"
      >
        {options.map((o) => (
          <option key={String(o.value)} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

export function EffectsPanel({ effectsConfig, onChange, onBackgroundUpload }: EffectsPanelProps) {
  const update = (path: string, value: unknown) => {
    const keys = path.split(".");
    const config = JSON.parse(JSON.stringify(effectsConfig || {}));
    let obj = config;
    for (let i = 0; i < keys.length - 1; i++) {
      if (!obj[keys[i]]) obj[keys[i]] = {};
      obj = obj[keys[i]];
    }
    obj[keys[keys.length - 1]] = value;
    onChange(config);
  };

  const toggle = (key: string) => {
    const section = (effectsConfig as Record<string, any>)?.[key] || {};
    update(`${key}.enabled`, !section.enabled);
  };

  const reactions = effectsConfig?.floating_reactions || {};
  const confetti = effectsConfig?.confetti || {};
  const lowerThird = effectsConfig?.lower_third || {};
  const pip = effectsConfig?.pip_avatar || {};
  const branding = effectsConfig?.branding || {};
  const selectedEmojis = reactions.emojis || ["❤️", "🔥", "😍"];

  const bgConfig = effectsConfig?.background || { type: "original" as const };

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-text">Effects</h3>

      {/* Compatibility note */}
      <div className="flex items-center gap-2 rounded-md bg-info/10 border border-info/20 px-3 py-1.5">
        <Info className="h-3 w-3 text-info shrink-0" />
        <span className="text-[10px] text-info">Object positions (product, text, stickers) are set in Scene Composer above.</span>
      </div>

      {/* Background Picker */}
      <div className={cn(
        "rounded-lg border transition-colors p-3",
        bgConfig.type && bgConfig.type !== "original"
          ? "border-accent/40 bg-accent/5"
          : "border-border bg-surface",
      )}>
        <div className="flex items-center gap-3 mb-2">
          <Palette className={cn("h-4 w-4 shrink-0", bgConfig.type !== "original" ? "text-accent" : "text-text-muted")} />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-text">Scene</p>
            <p className="text-[10px] text-text-muted">Replace avatar scene</p>
          </div>
        </div>
        <BackgroundPicker
          config={bgConfig}
          onChange={(bg) => update("background", bg)}
          onUpload={(file, mediaType) => onBackgroundUpload?.(file, mediaType)}
        />
      </div>

      {/* Avatar Fit */}
      <div className={cn(
        "rounded-lg border p-3 transition-colors",
        effectsConfig?.avatar_fit?.mode && effectsConfig.avatar_fit.mode !== "cover"
          ? "border-accent/40 bg-accent/5"
          : "border-border bg-surface",
      )}>
        <div className="flex items-center gap-3 mb-2">
          <Crop className="h-4 w-4 text-text-muted" />
          <div className="flex-1">
            <p className="text-xs font-medium text-text">Avatar Fit</p>
            <p className="text-[10px] text-text-muted">How the avatar fills the 9:16 frame</p>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {([
            { value: "cover", label: "Fill (crop)" },
            { value: "contain", label: "Fit (borders)" },
          ] as const).map((opt) => (
            <button
              key={opt.value}
              onClick={() => update("avatar_fit.mode", opt.value)}
              className={cn(
                "rounded border-2 p-2 text-[10px] transition-all",
                (effectsConfig?.avatar_fit?.mode || "cover") === opt.value
                  ? "border-accent bg-accent/10 text-text"
                  : "border-border bg-bg text-text-dim hover:border-accent/50",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
        {effectsConfig?.avatar_fit?.mode === "contain" && (
          <div className="mt-2">
            <SelectRow
              label="Background"
              value={effectsConfig.avatar_fit.bg_fill || "blur-sm"}
              options={[
                { value: "blur-sm", label: "Blurred avatar" },
                { value: "black", label: "Black" },
                { value: "transparent", label: "Transparent" },
              ]}
              onChange={(v) => update("avatar_fit.bg_fill", v)}
            />
          </div>
        )}
      </div>

      <EffectToggle
        icon={Smile}
        label="Floating Reactions"
        description="Animated emoji reactions"
        enabled={!!reactions.enabled}
        onToggle={() => toggle("floating_reactions")}
      >
        <div>
          <span className="text-[10px] text-text-dim block mb-1">Select emojis</span>
          <div className="flex flex-wrap gap-1">
            {EMOJI_PRESETS.map((emoji) => (
              <button
                key={emoji}
                onClick={() => {
                  const current = [...selectedEmojis];
                  const idx = current.indexOf(emoji);
                  if (idx >= 0) {
                    current.splice(idx, 1);
                  } else if (current.length < 5) {
                    current.push(emoji);
                  }
                  update("floating_reactions.emojis", current);
                }}
                className={cn(
                  "h-6 w-6 rounded text-sm flex items-center justify-center transition-colors",
                  selectedEmojis.includes(emoji)
                    ? "bg-accent/20 ring-1 ring-accent"
                    : "bg-surface hover:bg-border",
                )}
              >
                {emoji}
              </button>
            ))}
          </div>
        </div>
      </EffectToggle>

      <EffectToggle
        icon={PartyPopper}
        label="On Sale Confetti"
        description="Confetti burst on flash sales"
        enabled={!!confetti.enabled}
        onToggle={() => toggle("confetti")}
      />

      <EffectToggle
        icon={Subtitles}
        label="Lower Third Banner"
        description="Product name & price banner"
        enabled={!!lowerThird.enabled}
        onToggle={() => toggle("lower_third")}
      />

      <EffectToggle
        icon={PictureInPicture2}
        label="Talking Head"
        description="Picture-in-picture avatar window"
        enabled={!!pip.enabled}
        disabled
        onToggle={() => {}}
      >
        <p className="text-[10px] text-text-muted italic">Coming Soon</p>
      </EffectToggle>

      <EffectToggle
        icon={Stamp}
        label="Branding / Watermark"
        description="Custom watermark text"
        enabled={!!branding.enabled}
        onToggle={() => toggle("branding")}
      >
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-text-dim">Text</span>
          <input
            type="text"
            value={branding.text || ""}
            onChange={(e) => update("branding.text", e.target.value)}
            placeholder="@yourhandle"
            className="flex-1 h-6 rounded border border-border bg-bg px-1.5 text-[10px] text-text"
            maxLength={30}
          />
        </div>
      </EffectToggle>
    </div>
  );
}
