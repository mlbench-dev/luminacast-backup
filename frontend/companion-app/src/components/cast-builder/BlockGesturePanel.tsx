import { useState, useCallback, useEffect } from "react";
import { castsApi } from "@/lib/api";
import type { Cast, Block, RenderMode, PipEngine, UserVideoAsset } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { Move, Film, Info } from "lucide-react";
import { UserVideoPickerDialog } from "./UserVideoPickerDialog";
import { StockMediaPicker } from "@/components/common/StockMediaPicker";
import type { StockMediaItem } from "@/components/common/StockMediaPicker";
import { stockMediaApi } from "@/lib/api";
import { Globe, Loader2 } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { avatarLooksApi } from "@/lib/api";
import type { AvatarLook } from "@/lib/types";
import { BodyMotionFrameCarousel } from "./BodyMotionFrameCarousel";
import { ActionFrameCarousel } from "./ActionFrameCarousel";


const MOTION_PRESETS = [
  "Walking",
  "Pointing at product",
  "Holding up product",
  "Turning around",
  "Waving",
  "Standing confidently",
  "Sitting down",
  "Reaching forward",
];

interface BlockGesturePanelProps {
  cast: Cast;
  onCastUpdate?: () => void;
  onPipModeEnabled?: (blockId: string, blockPosition: number) => void;
}

const RENDER_MODES: { value: RenderMode; label: string }[] = [
  { value: "avatar_full", label: "Avatar" },
  { value: "pip", label: "Talking head" },
  { value: "voiceover", label: "Voiceover" },
  { value: "body_motion", label: "Action" },
];

export function BlockGesturePanel({ cast, onCastUpdate, onPipModeEnabled }: BlockGesturePanelProps) {
  const blocks = (cast.blocks || [])
    .sort((a, b) => (a.position ?? 0) - (b.position ?? 0));

  if (blocks.length === 0) return null;

  return (
    <div className="bg-zinc-900/80 border-t border-white/10 px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <Move className="w-4 h-4 text-purple-400" />
        <h3 className="text-sm font-medium text-white/80">Block Settings</h3>
      </div>
      <div className="space-y-3">
        {blocks.map((block, idx) => {
          const blockProduct =
            (block.product_id && cast.products?.find((p) => p.id === block.product_id)) ||
            cast.products?.[0] ||
            null;
          return (
            <BlockSettingsRow
              key={block.id}
              block={block}
              castId={cast.id}
              avatarId={cast.avatar_id || ""}
              index={idx}
              productName={blockProduct?.name || null}
              onCastUpdate={onCastUpdate}
              onPipModeEnabled={onPipModeEnabled}
            />
          );
        })}
      </div>
    </div>
  );
}

function BlockSettingsRow({
  block,
  castId,
  avatarId,
  index,
  productName,
  onCastUpdate,
  onPipModeEnabled,
}: {
  block: Block;
  castId: string;
  avatarId: string;
  index: number;
  productName?: string | null;
  onCastUpdate?: () => void;
  onPipModeEnabled?: (blockId: string, blockPosition: number) => void;
}) {
  const variant = block.variants?.[0];
  const [motionValue, setMotionValue] = useState(variant?.motion_prompt || "");
  const [saving, setSaving] = useState(false);
  const [renderMode, setRenderMode] = useState<RenderMode>(
    (block.render_mode as RenderMode) || "avatar_full"
  );
  const [pipEngine, setPipEngine] = useState<PipEngine>(
    (block.pip_engine as PipEngine) || "infinitetalk_rendered"
  );
  const [videoAssetId, setVideoAssetId] = useState<string | null>(
    block.user_video_asset_id || null
  );
  const [pickerOpen, setPickerOpen] = useState(false);
  const [stockPickerOpen, setStockPickerOpen] = useState(false);
  const [importingStock, setImportingStock] = useState(false);
  const [lookId, setLookId] = useState<string | null>(block.avatar_look_id || null);
  const [bodyMotionStartId, setBodyMotionStartId] = useState<string | null>(block.body_motion_start_look_id || null);
  const [bodyMotionEndId, setBodyMotionEndId] = useState<string | null>(block.body_motion_end_look_id || null);
  const [bodyMotionPrompt, setBodyMotionPrompt] = useState(block.body_motion_prompt || "");

  // Check MuseTalk availability
  const { data: musetalkStatus } = useQuery({
    queryKey: ["musetalk-status"],
    queryFn: async () => {
      const resp = await fetch("/api/system/musetalk-status");
      return resp.json() as Promise<{ available: boolean; current_model: string | null }>;
    },
    staleTime: 30000,
  });
  const musetalkAvailable = musetalkStatus?.available ?? false;

  const { data: looksData } = useQuery({
    queryKey: ["avatar-looks", avatarId],
    queryFn: () => avatarLooksApi.list(avatarId),
    enabled: !!avatarId,
  });
  const availableLooks: AvatarLook[] = (looksData?.looks || []).filter((l: AvatarLook) => l.status === "ready");

  const saveField = useCallback(
    async (data: Record<string, unknown>) => {
      setSaving(true);
      try {
        await castsApi.updateBlock(castId, block.id, data);
        toast({
          title: "Saved",
          description: `Block ${index + 1} updated`,
        });
        onCastUpdate?.();
      } catch (err: any) {
        toast({
          title: "Save failed",
          description:
            err?.response?.data?.detail || err.message || "Unknown error",
          variant: "destructive",
        });
      } finally {
        setSaving(false);
      }
    },
    [castId, block.id, index, onCastUpdate]
  );

  const handleMotionBlur = useCallback(async () => {
    if (!variant) return;
    const original = variant.motion_prompt || "";
    if (motionValue === original) return;
    await saveField({ motion_prompt: motionValue });
  }, [motionValue, variant, saveField]);

  const handleModeChange = useCallback(
    async (mode: RenderMode) => {
      if ((mode === "voiceover" || mode === "pip") && !videoAssetId) {
        setRenderMode(mode);
        setPickerOpen(true);
        return;
      }
      setRenderMode(mode);
      await saveField({ render_mode: mode });
      if (mode === "pip") {
        onPipModeEnabled?.(block.id, block.position ?? 0);
      }
    },
    [videoAssetId, saveField, block.id, block.position, onPipModeEnabled]
  );

  const handlePipEngineChange = useCallback(
    async (engine: PipEngine) => {
      setPipEngine(engine);
      await saveField({ pip_engine: engine });
    },
    [saveField]
  );

  const handleVideoSelect = useCallback(
    async (video: UserVideoAsset) => {
      setVideoAssetId(video.id);
      await saveField({
        render_mode: renderMode,
        user_video_asset_id: video.id,
      });
      if (renderMode === "pip") {
        onPipModeEnabled?.(block.id, block.position ?? 0);
      }
    },
    [renderMode, saveField, block.id, block.position, onPipModeEnabled]
  );

  const handleStockSelect = useCallback(
    async (media: StockMediaItem) => {
      try {
        const result = await stockMediaApi.importMedia({
          url: media.type === "video" ? (media as any).video_files?.[0]?.link || media.src : media.src,
          type: media.type,
          pexels_id: media.id,
          name: (media as any).alt || media.photographer || "Stock media",
        });
        if (result?.id) {
          setVideoAssetId(result.id);
          await saveField({
            render_mode: renderMode,
            user_video_asset_id: result.id,
          });
        }
        setStockPickerOpen(false);
        toast({ title: "Stock media imported" });
      } catch (err: any) {
        toast({ title: "Failed to import stock media", description: err?.message, variant: "destructive" });
      }
    },
    [renderMode, saveField]
  );

  const showMotionPrompt = renderMode !== "voiceover";

  return (
    <div className="border border-white/10 rounded-lg p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-white/50 font-medium">
          Block {(block.position ?? 0) + 1}{" "}
          <span className="text-white/30">· {block.type}</span>
        </span>
        {saving && <span className="text-xs text-purple-400">Saving...</span>}
      </div>

      {/* Render mode segmented control */}
      <div className="flex gap-1 bg-white/5 rounded-md p-0.5">
        {RENDER_MODES.map((m) => (
          <button
            key={m.value}
            onClick={() => handleModeChange(m.value)}
            className={`flex-1 text-xs py-1.5 px-2 rounded transition-colors ${
              renderMode === m.value
                ? "bg-purple-600 text-white"
                : "text-white/50 hover:text-white/80"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* PIP Engine sub-selector — only when PIP mode */}
      {renderMode === "pip" && (
        <div className="space-y-1.5">
          <div className="flex gap-1 bg-white/5 rounded-md p-0.5">
            <button
              onClick={() => handlePipEngineChange("infinitetalk_rendered")}
              className={`flex-1 text-xs py-1.5 px-2 rounded transition-colors ${
                pipEngine === "infinitetalk_rendered"
                  ? "bg-blue-600 text-white"
                  : "text-white/50 hover:text-white/80"
              }`}
            >
              Rendered PIP
            </button>
            <button
              onClick={() => musetalkAvailable && handlePipEngineChange("musetalk_live")}
              disabled={!musetalkAvailable}
              title={!musetalkAvailable ? "Live Render is currently offline." : ""}
              className={`flex-1 text-xs py-1.5 px-2 rounded transition-colors ${
                pipEngine === "musetalk_live"
                  ? "bg-green-600 text-white"
                  : musetalkAvailable
                  ? "text-white/50 hover:text-white/80"
                  : "text-white/20 cursor-not-allowed"
              }`}
            >
              Live PIP
            </button>
          </div>
          <div className="flex items-start gap-1 text-[10px] text-white/40 px-1">
            <Info className="w-3 h-3 mt-0.5 shrink-0" />
            <span>
              {pipEngine === "infinitetalk_rendered"
                ? "Best quality \u00b7 Renders offline (2-15 min/block)"
                : "Real-time \u00b7 Lower quality (~5 sec/block)"}
            </span>
          </div>
          <p className="text-[10px] text-white/30 px-1">
            Rendered PIP uses Studio Render for broadcast quality. Live PIP uses Live Render for instant rendering at lower fidelity — best for fast iteration and live broadcasts.
          </p>
        </div>
      )}

      {/* Video asset indicator for voiceover/pip */}
      {(renderMode === "voiceover" || renderMode === "pip") && (
        <button
          onClick={() => setPickerOpen(true)}
          className="w-full flex items-center gap-2 p-2 rounded-md bg-white/5 border border-white/10 hover:bg-white/10 transition-colors text-left"
        >
          <Film className="w-4 h-4 text-purple-400 shrink-0" />
          <span className="text-xs text-white/70 truncate flex-1">
            {videoAssetId ? `Video: ${videoAssetId.slice(0, 12)}...` : "Select a video..."}
          </span>
        </button>
      )}

      {/* Look picker */}
      {(renderMode === "avatar_full" || renderMode === "pip") && availableLooks.length > 1 && (
        <select
          value={lookId || availableLooks.find((l) => l.is_default)?.id || ""}
          onChange={async (e) => {
            setLookId(e.target.value);
            await saveField({ avatar_look_id: e.target.value });
          }}
          className="w-full text-xs bg-white/5 border border-white/10 rounded-md p-2 text-white/80"
        >
          {availableLooks.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}{l.is_default ? " (default)" : ""}
            </option>
          ))}
        </select>
      )}

      {/* Action / Body Motion settings.
          New avatar_action blocks render with FLUX-Kontext-generated SCENE
          frames via ActionFrameCarousel; legacy avatar_acting blocks fall
          back to the original body-shot carousel until the SQL migration
          rewrites their category to avatar_action. */}
      {renderMode === "body_motion" && (
        <div className="space-y-3">
          {(block.category === "avatar_action" ||
            block.category === "avatar_motion" ||
            !block.category) ? (
            <ActionFrameCarousel
              castId={castId}
              blockId={block.id}
              startLookId={bodyMotionStartId}
              endLookId={bodyMotionEndId}
              startPromptSeed={
                (block as any).action_start_prompt ||
                (block as any).body_motion_start_prompt ||
                null
              }
              endPromptSeed={
                (block as any).action_end_prompt ||
                (block as any).body_motion_end_prompt ||
                null
              }
              productName={productName || null}
              onSelectionChange={async (kind, lookId) => {
                const prev = kind === "start" ? bodyMotionStartId : bodyMotionEndId;
                if (kind === "start") setBodyMotionStartId(lookId);
                else setBodyMotionEndId(lookId);
                try {
                  await castsApi.updateBlock(castId, block.id, {
                    [kind === "start"
                      ? "body_motion_start_look_id"
                      : "body_motion_end_look_id"]: lookId,
                  });
                  onCastUpdate?.();
                } catch (err) {
                  if (kind === "start") setBodyMotionStartId(prev);
                  else setBodyMotionEndId(prev);
                  throw err;
                }
              }}
            />
          ) : (
            <BodyMotionFrameCarousel
              castId={castId}
              blockId={block.id}
              startLookId={bodyMotionStartId}
              endLookId={bodyMotionEndId}
              startPromptSeed={(block as any).body_motion_start_prompt || null}
              endPromptSeed={(block as any).body_motion_end_prompt || null}
              onSelectionChange={async (kind, lookId) => {
                const prev = kind === "start" ? bodyMotionStartId : bodyMotionEndId;
                if (kind === "start") setBodyMotionStartId(lookId);
                else setBodyMotionEndId(lookId);
                try {
                  await castsApi.updateBlock(castId, block.id, {
                    [kind === "start"
                      ? "body_motion_start_look_id"
                      : "body_motion_end_look_id"]: lookId,
                  });
                  onCastUpdate?.();
                } catch (err) {
                  if (kind === "start") setBodyMotionStartId(prev);
                  else setBodyMotionEndId(prev);
                  throw err;
                }
              }}
            />
          )}

          {/* Motion prompt chips — describes the in-between motion verb */}
          <div>
            <label className="text-[10px] text-white/40 mb-1 block">Motion Prompt</label>
            <div className="flex flex-wrap gap-1 mb-1.5">
              {MOTION_PRESETS.map((preset) => (
                <button
                  key={preset}
                  onClick={() => {
                    const lower = preset.toLowerCase();
                    setBodyMotionPrompt(lower);
                    saveField({ body_motion_prompt: lower });
                  }}
                  className={`text-[10px] px-2 py-0.5 rounded-full border transition-colors ${
                    bodyMotionPrompt === preset.toLowerCase()
                      ? "bg-purple-600/30 border-purple-500/50 text-purple-300"
                      : "bg-white/5 border-white/10 text-white/50 hover:text-white/80"
                  }`}
                >
                  {preset}
                </button>
              ))}
            </div>
            <textarea
              data-testid="body-motion-prompt"
              value={bodyMotionPrompt}
              onChange={(e) => setBodyMotionPrompt(e.target.value)}
              onBlur={async () => {
                if (bodyMotionPrompt !== (block.body_motion_prompt || "")) {
                  await saveField({ body_motion_prompt: bodyMotionPrompt });
                }
              }}
              placeholder="e.g. walks confidently across the frame, smiling"
              rows={2}
              className="w-full bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm text-white/90 placeholder:text-white/30 resize-none focus:outline-hidden focus:border-purple-500/50"
            />
          </div>

          {/* Cost estimate */}
          <div className="flex items-center gap-1.5 px-1">
            <Info className="w-3 h-3 text-white/30 shrink-0" />
            <span className="text-[10px] text-white/40">
              Estimated cost: ~$0.65 per block (AI render + lip sync)
            </span>
          </div>
        </div>
      )}

      {/* Motion prompt (for non-body-motion modes) */}
      {showMotionPrompt && renderMode !== "body_motion" && (
        <textarea
          value={motionValue}
          onChange={(e) => setMotionValue(e.target.value)}
          onBlur={handleMotionBlur}
          placeholder="e.g. slow zoom in, pan left to right..."
          rows={2}
          className="w-full bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm text-white/90 placeholder:text-white/30 resize-none focus:outline-hidden focus:border-purple-500/50"
        />
      )}

      <UserVideoPickerDialog
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={handleVideoSelect}
        selectedId={videoAssetId}
      />
      <StockMediaPicker
        open={stockPickerOpen}
        onClose={() => setStockPickerOpen(false)}
        onSelect={handleStockSelect}
        mediaType="video"
      />
    </div>
  );
}
