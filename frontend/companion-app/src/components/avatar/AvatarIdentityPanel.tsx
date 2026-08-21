import { useState, useRef, useCallback } from "react";
import { ChevronDown, ChevronUp, Play, Pause, Volume2 } from "lucide-react";
import { avatarApi } from "@/lib/api";
import type { Avatar } from "@/lib/types";
import { cn } from "@/lib/cn";

interface AvatarIdentityPanelProps {
  avatar: Avatar;
  /** Compact mode for narrow/mobile contexts */
  compact?: boolean;
  className?: string;
  /**
   * Optional live overrides for in-progress edits. When the user has selected
   * a thumbnail or typed a description in the current step but hasn't yet
   * persisted it to the avatar record, these props let the panel reflect that
   * state instantly so the left column always mirrors what the user sees on
   * the right.
   */
  faceUrlOverride?: string | null;
  nameOverride?: string;
  descriptionOverride?: string;
  bodyDescriptionOverride?: string;
}

export function AvatarIdentityPanel({
  avatar,
  compact,
  className,
  faceUrlOverride,
  nameOverride,
  descriptionOverride,
  bodyDescriptionOverride,
}: AvatarIdentityPanelProps) {
  const [descExpanded, setDescExpanded] = useState(false);
  const [bodyExpanded, setBodyExpanded] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioLoading, setAudioLoading] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const hasVoice = !!avatar.voice_id;
  // Override > persisted. Empty string overrides count as “user cleared it”
  // and therefore should hide the field; only undefined/null fall through.
  const faceUrl = faceUrlOverride ?? avatar.face_image_url;
  const displayName = nameOverride ?? avatar.name;
  const displayDescription = descriptionOverride ?? avatar.description;
  const displayBodyDescription = bodyDescriptionOverride ?? avatar.body_description;
  const interests = (avatar.target_audience as { interests?: string[] })?.interests ?? [];
  const ageRange = (avatar.target_audience as { age_range?: string })?.age_range ?? "";

  const handlePlayVoice = useCallback(async () => {
    if (!avatar.id) return;

    // If already playing, pause
    if (isPlaying && audioRef.current) {
      audioRef.current.pause();
      setIsPlaying(false);
      return;
    }

    // If audio element exists and is paused, resume
    if (audioRef.current && audioRef.current.src) {
      audioRef.current.play();
      setIsPlaying(true);
      return;
    }

    // Fetch and play
    setAudioLoading(true);
    try {
      const { audio_url } = await avatarApi.aiGetLockedVoiceAudio(avatar.id);
      const audio = new Audio(audio_url);
      audioRef.current = audio;
      audio.onended = () => setIsPlaying(false);
      audio.onerror = () => {
        setIsPlaying(false);
        setAudioLoading(false);
      };
      await audio.play();
      setIsPlaying(true);
    } catch {
      setIsPlaying(false);
    } finally {
      setAudioLoading(false);
    }
  }, [avatar.id, isPlaying]);

  const imageSize = compact ? "w-[200px] h-[200px]" : "w-[280px] h-[280px]";

  return (
    <div className={cn("flex flex-col items-center gap-3", className)}>
      {/* Face image */}
      {faceUrl ? (
        <img
          src={faceUrl}
          alt={avatar.name || "Avatar"}
          className={cn(imageSize, "rounded-2xl object-cover border border-border")}
        />
      ) : (
        <div
          className={cn(
            imageSize,
            "rounded-2xl bg-surface border border-border flex items-center justify-center text-muted-foreground text-sm"
          )}
        >
          No face yet
        </div>
      )}

      {/* Name */}
      {displayName && (
        <h2 className="text-lg font-semibold text-foreground text-center leading-tight break-words max-w-full">
          {displayName}
        </h2>
      )}

      {/* Age range + interests line */}
      {(ageRange || interests.length > 0) && (
        <p className="text-xs text-muted-foreground text-center leading-snug">
          {[ageRange, ...interests.slice(0, 3)].filter(Boolean).join(" · ")}
        </p>
      )}

      {/* Avatar description */}
      {displayDescription && (
        <div className="w-full">
          <p
            className={cn(
              "text-xs text-muted-foreground leading-relaxed",
              !descExpanded && "line-clamp-2"
            )}
          >
            {displayDescription}
          </p>
          {displayDescription.length > 120 && (
            <button
              onClick={() => setDescExpanded(!descExpanded)}
              className="text-xs font-medium mt-0.5 flex items-center gap-0.5"
              style={{ color: "var(--accent-active)" }}
            >
              {descExpanded ? (
                <>Show less <ChevronUp className="h-3 w-3" /></>
              ) : (
                <>Show more <ChevronDown className="h-3 w-3" /></>
              )}
            </button>
          )}
        </div>
      )}

      {/* Body description */}
      {displayBodyDescription && (
        <div className="w-full">
          <p className="text-[10px] font-medium text-muted-foreground/70 uppercase tracking-wider mb-0.5">
            Body
          </p>
          <p
            className={cn(
              "text-xs text-muted-foreground leading-relaxed",
              !bodyExpanded && "line-clamp-2"
            )}
          >
            {displayBodyDescription}
          </p>
          {displayBodyDescription.length > 120 && (
            <button
              onClick={() => setBodyExpanded(!bodyExpanded)}
              className="text-xs font-medium mt-0.5 flex items-center gap-0.5"
              style={{ color: "var(--accent-active)" }}
            >
              {bodyExpanded ? (
                <>Show less <ChevronUp className="h-3 w-3" /></>
              ) : (
                <>Show more <ChevronDown className="h-3 w-3" /></>
              )}
            </button>
          )}
        </div>
      )}

      {/* Voice preview button */}
      {hasVoice && (
        <button
          onClick={handlePlayVoice}
          disabled={audioLoading}
          className={cn(
            "w-full flex items-center justify-center gap-2 rounded-lg py-2 px-3",
            "border border-border text-sm font-medium transition-colors",
            "hover:bg-surface-hover disabled:opacity-50"
          )}
        >
          {audioLoading ? (
            <Volume2 className="h-4 w-4 animate-pulse" />
          ) : isPlaying ? (
            <Pause className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          <span>{isPlaying ? "Pause voice" : "Play voice preview"}</span>
        </button>
      )}
    </div>
  );
}
