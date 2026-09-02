import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, UserCircle, Package, Wand2, Radio, Clock, Monitor, Pencil, Sparkles, ImageIcon, Mic, Film, Layers, BarChart3, ArrowLeftRight, Camera, Check } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { avatarApi, avatarLooksApi, castsApi, productsApi } from "@/lib/api";
import { AvatarLookPicker } from "@/components/cast-builder/scriptphase/AvatarLookPicker";
import { MusicTrackPickerModal } from "@/components/cast-builder/MusicTrackPickerModal";
import { UserVideoPickerDialog } from "@/components/cast-builder/UserVideoPickerDialog";
import { LiveReferenceCard } from "@/components/avatar/LiveReferenceCard";
import { AvatarStatus, type Avatar, type AvatarLook, type ProductWithAssets, type Cast, type CastTemplate, type UserVideoAsset } from "@/lib/types";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { toast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";
import { RenderLockBanner } from "@/components/cast-builder/RenderLockBanner";

// Cap the cast name so a pasted essay can't break page/card/header layout.
// Kept in sync with the backend (CastCreate/CastPatchRequest name max_length).
const CAST_NAME_MAX_LENGTH = 80;

// Render billing is metered in MINUTES, not dollars. Each tier applies a
// multiplier to the video's runtime (simple ×1.0, HD ×1.4, HD+ ×2.0); those
// billable minutes come out of the plan's monthly allowance first, then PAYG
// credits, then overage. See services/billing_service.compute_billable_minutes.
const QUALITY_MINUTE_MULTIPLIER: Record<string, number> = {
  simple: 1.0,
  hd: 1.4,
  hd_plus: 2.0,
};
// standard & quick bill at the same rate — quick is cheaper only because it
// produces a SHORTER video (fewer minutes), not a lower per-minute rate.
const PRODUCTION_MINUTE_MULTIPLIER: Record<string, number> = {
  quick: 1.0,
  standard: 1.0,
  premium: 1.5,
};

const LAYOUT_OPTIONS = [
  { value: "9:16", label: "Vertical 9:16", desc: "1080×1920", icon: "📱" },
  { value: "16:9", label: "Horizontal 16:9", desc: "1920×1080", icon: "🖥" },
  { value: "1:1", label: "Square 1:1", desc: "1080×1080", icon: "⬜" },
  { value: "4:5", label: "4:5 Feed", desc: "1080×1350", icon: "📷" },
] as const;

const BROLL_SOURCE_OPTIONS = [
  {
    value: "stock",
    label: "Generic stock",
    desc: "Pexels clips matched to your script.",
    hint: "Fast",
    Icon: Film,
  },
  {
    value: "ai_generated",
    label: "AI-generated from product",
    desc: "Product-only shots made from your product's own photo.",
    hint: "Slower · runs in the background",
    Icon: Sparkles,
  },
] as const;

const PLATFORM_BY_LAYOUT: Record<string, { value: string; label: string }[]> = {
  "9:16": [
    { value: "tiktok", label: "TikTok" },
    { value: "instagram_reels", label: "Instagram Reels" },
    { value: "youtube_shorts", label: "YouTube Shorts" },
  ],
  "16:9": [
    { value: "youtube", label: "YouTube" },
    { value: "twitter", label: "Twitter / X" },
    { value: "linkedin", label: "LinkedIn" },
  ],
  "1:1": [
    { value: "instagram_feed", label: "Instagram Feed" },
    { value: "facebook_feed", label: "Facebook Feed" },
    { value: "linkedin_feed", label: "LinkedIn Feed" },
  ],
  "4:5": [
    { value: "instagram_feed_45", label: "Instagram Feed" },
    { value: "facebook_feed_45", label: "Facebook Feed" },
  ],
};

const DURATION_MARKS_SECONDS = [5, 15, 30, 60, 120, 180, 300, 600, 720];
const DURATION_MARKS_MINUTES = [1, 2, 5, 10, 30, 60, 120];
// LIVE casts are long-form host-style content (voiceover + b-roll), so the
// duration ceiling lifts from 720s to 2 hours. Marks run 1-min → 2-hour.
const DURATION_MARKS_SECONDS_LIVE = [60, 120, 300, 600, 900, 1800, 3600, 5400, 7200];
// Default duration applied when switching INTO live mode (30 min).
const LIVE_DEFAULT_DURATION = 1800;
// Upper bound on the duration slider, by mode. Recorded keeps the legacy
// 720s cap; live opens up to 2 hours.
const RECORDED_MAX_SECONDS = 720;
const LIVE_MAX_SECONDS = 7200;

// Per-template icon for the Stage-1 picker cards. Keyed by template id from
// GET /api/casts/templates. Unknown ids fall back to the generic Film icon.
const TEMPLATE_ICONS: Record<string, LucideIcon> = {
  talking_head_hook: Mic,
  demo_heavy: Film,
  multi_angle_story: Layers,
  social_proof_stack: BarChart3,
  before_after_reveal: ArrowLeftRight,
  mic_on_creator_vlog: Camera,
};

// Stable string form of every field that shapes the generated outline/script.
// On a return trip to Setup we diff the current form against the snapshot
// taken at hydration; if anything here changed, hitting "Generate Script"
// re-submits these (PATCH now accepts the structural ones too, while the cast
// is still pre-render) and regenerates. Layout/platforms/music/name/background
// aren't here — they don't change the script text.
function serializeRegenFields(f: {
  description: string;
  durationManual: boolean;
  durationTarget: number;
  avatar: string | null;
  products: string[];
  productionLevel: string;
  castType: string;
  template: string | null;
  brollMediaSource: string;
}): string {
  // NOTE: `quality` is deliberately NOT here. It only changes the next
  // render's resolution/price, never the script — so changing it alone
  // must not trigger the "Regenerate script?" prompt.
  return JSON.stringify({
    description: f.description.trim(),
    durationTarget: f.durationManual ? f.durationTarget : null,
    avatar: f.avatar,
    products: [...f.products].sort(),
    productionLevel: f.productionLevel,
    castType: f.castType,
    template: f.template,
    brollMediaSource: f.brollMediaSource,
  });
}

interface SetupPhaseProps {
  // Present when navigating back to Setup after the cast already exists
  // (e.g. via the wizard's Back button from Script/Arrange). Used to
  // rehydrate the form instead of showing blank defaults for a cast that
  // already has selections.
  cast?: Cast | null;
  // `wasExisting` tells the caller whether this cast was freshly created
  // just now (false) vs. an already-existing cast that was just patched
  // after the user navigated back to Setup and hit Continue again (true).
  // The parent needs this to decide whether to auto-trigger script
  // generation — doing that unconditionally would silently overwrite a
  // script the user already reviewed/edited on an existing cast.
  //
  // `forceRegenerate` overrides that: it's set when the user came back to
  // Setup, changed a script-affecting field (brief, template, avatar,
  // products, quality, production level, duration, cast type), and hit
  // "Generate Script" again — a deliberate request to rebuild the outline +
  // script from the new settings, so the parent regenerates even though the
  // cast already existed. Without this the only way to pick up Setup changes
  // was the Script tab's separate "Regenerate" button.
  onCreated: (cast: Cast, wasExisting?: boolean, forceRegenerate?: boolean) => void;
  // True while a render is actively queued/baking/composing for this cast.
  // The render task reads several fields (quality, duration, script/voice)
  // live rather than from a frozen snapshot, so an edit here mid-render can
  // produce a video that's part old content, part new — lock the form while
  // this is true rather than let that race happen silently.
  renderInProgress?: boolean;
  onCancelRender?: () => void;
}

export function SetupPhase({ cast, onCreated, renderInProgress, onCancelRender }: SetupPhaseProps) {
  const qc = useQueryClient();
  // Left blank by default — name is optional. The backend's
  // _generate_cast_name (routers/casts/crud.py) already generates a
  // correctly unique "Cast {Mon}{Day}-{N}" per-user daily sequence when
  // `name` comes through empty; pre-filling this field client-side with a
  // fixed "-1" suffix (the old generateAutoName()) is what caused every
  // cast created the same day to collide on an identical default name —
  // req.name was never actually empty, so the backend's real counter-based
  // generator never got a chance to run.
  const [castName, setCastName] = useState("");
  const [nameEditing, setNameEditing] = useState(false);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const [castType, setCastType] = useState<"recorded" | "live">("recorded");
  // User-uploaded b-roll clips for LIVE casts. Threaded onto the cast-create
  // POST as `user_video_ids`; the upload card is only shown in live mode.
  const [selectedUserVideos, setSelectedUserVideos] = useState<UserVideoAsset[]>([]);
  const [videoPickerOpen, setVideoPickerOpen] = useState(false);
  // Stage-1 creative template. null = "Auto / let AI choose" (default). When a
  // template is picked, its id is sent as `template_id` on create and the
  // outline generator is constrained to the template's structure.
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [selectedAvatar, setSelectedAvatar] = useState<string | null>(null);
  // Cast-wide avatar background look. Null = use the avatar's own default.
  // Persisted on Cast.default_avatar_look_id at create time and copied to
  // every block's avatar_look_id by the smart outline pipeline.
  const [selectedLookId, setSelectedLookId] = useState<string | null>(null);
  const [selectedProducts, setSelectedProducts] = useState<string[]>([]);
  const [quality, setQuality] = useState<string>("hd");
  const [outputFormat, setOutputFormat] = useState("9:16");
  const [targetPlatforms, setTargetPlatforms] = useState<string[]>(["tiktok", "instagram_reels", "youtube_shorts"]);
  const [description, setDescription] = useState("");
  const [durationManual, setDurationManual] = useState(false);
  const [durationTarget, setDurationTarget] = useState(60);
  const [durationUnit, setDurationUnit] = useState<"seconds" | "minutes">("seconds");
  // Per-mode duration memory — switching between recorded/live preserves the
  // last value the user had in each mode so the slider doesn't jump around.
  const lastRecordedDuration = useRef(60);
  const lastLiveDuration = useRef(LIVE_DEFAULT_DURATION);
  // Auto Cast: when ON, the AI decides the avatar / PIP split per block
  // (InfiniteTalk full-frame vs MuseTalk small/medium PIP), picks stock
  // b-roll, hook style, transitions. Quality is fixed at a sensible HD
  // default and the LQ↔HQ slider is hidden — the user is letting AI drive.
  //
  // When OFF (manual mode), the user picks the quality preset themselves
  // via the LQ→HQ slider, sees the live cost estimate, and gets the legacy
  // avatar-only outline (every block is the avatar talking to camera).
  const [autoCast, setAutoCast] = useState(true);
  // Production level — quick | standard | premium. Replaces the legacy
  // AI-Plan chip preview. Sent to the backend as `production_level` and
  // ultimately constrains which block categories the outline generator
  // is allowed to produce.
  const [productionLevel, setProductionLevel] = useState<"quick" | "standard" | "premium">("standard");
  // Background-music choice: "off" | "auto" | "custom" | "track_id:<id>"
  // (the last is legacy — a handful of fixed placeholder tracks from before
  // the real library was wired in; kept only so old casts still resolve).
  // Default "auto" (mood-driven AI generation). Persisted as
  // `music_track_choice`; "custom" means a real library track was picked
  // via MusicTrackPickerModal, with its url/mood/tags already stored on
  // background_music_url/background_music_mood/background_music_tags.
  const [musicChoice, setMusicChoice] = useState<string>("auto");
  // Default visual source for stock_photo/stock_video (pure B-roll) blocks
  // with a product attached: "stock" auto-selects from Pexels (existing
  // behavior); "ai_generated" instead generates a product-only photo/video
  // from the product's own reference photo (see services/product_ai_media.py).
  // Either way the user can still override a specific block's visual in the
  // Script tab (VisualSourcePicker).
  const [brollMediaSource, setBrollMediaSource] = useState<"stock" | "ai_generated">("stock");
  const [musicTrackPickerOpen, setMusicTrackPickerOpen] = useState(false);
  const [pickedTrack, setPickedTrack] = useState<{ url: string; mood: string; name: string } | null>(null);
  const { data: castTemplates } = useQuery({
    queryKey: ["cast-templates"],
    queryFn: () => castsApi.templates(),
    staleTime: 60 * 60 * 1000,
  });

  // Rehydrate the form from an already-created cast — e.g. the user
  // generated a script/outline, hit Back, and landed here again. Without
  // this, every field above falls back to its blank/default useState value
  // even though the cast already has real selections. Guarded by a ref (not
  // just `cast` in the deps) so it only ever runs once per mount — this is a
  // one-time hydration, not a live sync, and shouldn't fight the user's
  // subsequent edits on every re-render.
  const hydratedFromCastRef = useRef(false);
  // Snapshot of the script-affecting fields as they were when we hydrated an
  // existing cast. On resubmit we diff against this to decide whether hitting
  // "Generate Script" should rebuild the outline + script (see createMutation).
  const regenSnapshotRef = useRef<string | null>(null);
  // Set inside mutationFn when the user confirms a regeneration; read in
  // onSuccess to tell the parent to rebuild.
  const regenRequestedRef = useRef(false);
  useEffect(() => {
    if (!cast || hydratedFromCastRef.current) return;
    hydratedFromCastRef.current = true;
    if (cast.name) setCastName(cast.name.slice(0, CAST_NAME_MAX_LENGTH));
    if (cast.cast_type) setCastType(cast.cast_type);
    setSelectedTemplate(cast.template_id ?? null);
    setSelectedAvatar(cast.avatar_id ?? null);
    setSelectedLookId(cast.default_avatar_look_id ?? null);
    setSelectedProducts((cast.products || []).map((p) => p.id));
    if (cast.quality) setQuality(cast.quality);
    if (cast.output_format) setOutputFormat(cast.output_format);
    if (cast.target_platforms && cast.target_platforms.length > 0) {
      setTargetPlatforms(cast.target_platforms);
    }
    if (cast.description) setDescription(cast.description);
    if (cast.duration_target_seconds != null) {
      setDurationManual(true);
      setDurationTarget(cast.duration_target_seconds);
    }
    if (cast.production_level) {
      setProductionLevel(cast.production_level as "quick" | "standard" | "premium");
      if (cast.production_level !== "standard") setAutoCast(false);
    }
    if (cast.music_track_choice) setMusicChoice(cast.music_track_choice);
    if (cast.broll_media_source) setBrollMediaSource(cast.broll_media_source);
    if (cast.music_track_choice === "custom" && (cast as any).background_music_url) {
      const mood = (cast as any).background_music_mood || "";
      setPickedTrack({
        url: (cast as any).background_music_url,
        mood,
        name: mood ? `Custom track (${mood})` : "Custom track",
      });
    }
    regenSnapshotRef.current = serializeRegenFields({
      description: cast.description || "",
      durationManual: cast.duration_target_seconds != null,
      durationTarget: cast.duration_target_seconds ?? 60,
      avatar: cast.avatar_id ?? null,
      products: (cast.products || []).map((p) => p.id),
      productionLevel: (cast.production_level as string) || "standard",
      castType: cast.cast_type || "recorded",
      template: cast.template_id ?? null,
      brollMediaSource: cast.broll_media_source || "stock",
    });
  }, [cast]);

  // When Auto Cast is ON we lock quality to HD and hide the slider — the
  // moment the user flips it on we snap quality back to the safe default
  // so the cast that gets created doesn't carry over a stale LQ/HQ+ pick.
  useEffect(() => {
    if (autoCast && quality !== "hd") {
      setQuality("hd");
    }
  }, [autoCast, quality]);

  // Rough billable-minute estimate for a manual-duration render, shown so the
  // user knows a render spends MINUTES (not dollars): runtime × quality mult
  // × production mult. Only meaningful when the user set an explicit target;
  // Auto mode's length isn't known here. Mirrors
  // services/billing_service.compute_billable_minutes.
  const estimatedRenderMinutes = useMemo(() => {
    if (autoCast || !durationManual || !durationTarget) return null;
    const qMult = QUALITY_MINUTE_MULTIPLIER[quality] ?? 1.0;
    const pMult = PRODUCTION_MINUTE_MULTIPLIER[productionLevel] ?? 1.0;
    return Math.max(0.1, (durationTarget / 60) * qMult * pMult);
  }, [autoCast, durationManual, durationTarget, quality, productionLevel]);

  // When layout changes, reset platforms to all in that family
  const handleLayoutChange = useCallback((layout: string) => {
    setOutputFormat(layout);
    const platforms = PLATFORM_BY_LAYOUT[layout] || [];
    setTargetPlatforms(platforms.map(p => p.value));
  }, []);

  const togglePlatform = useCallback((platform: string) => {
    setTargetPlatforms(prev => {
      if (prev.includes(platform)) {
        // Don't allow deselecting all
        if (prev.length <= 1) return prev;
        return prev.filter(p => p !== platform);
      }
      return [...prev, platform];
    });
  }, []);

  // Switching cast type is the hero decision of Stage 1. LIVE pre-configures
  // the cast for a long-form voiceover-with-b-roll flow:
  //   - duration ceiling lifts to 2h, default jumps to 30 min
  //   - Smart Cast defaults OFF so the user is nudged to configure
  //   - duration is remembered per mode so toggling back restores the prior value
  const handleCastTypeChange = useCallback((next: "recorded" | "live") => {
    setCastType((prev) => {
      if (prev === next) return prev;
      // Stash the current duration under the outgoing mode so a later switch
      // back restores it. (Pure bookkeeping on refs — safe inside the updater.)
      if (prev === "live") lastLiveDuration.current = durationTarget;
      else lastRecordedDuration.current = durationTarget;
      return next;
    });
    if (next === "live") {
      setDurationTarget(lastLiveDuration.current);
      setDurationUnit("minutes");
      setAutoCast(false);
    } else {
      setDurationTarget(lastRecordedDuration.current);
    }
  }, [durationTarget]);

  const { data: avatarsData, isLoading: avatarsLoading } = useQuery({
    queryKey: ["avatars-all"],
    queryFn: () => avatarApi.list(),
  });

  const { data: productsData, isLoading: productsLoading } = useQuery({
    queryKey: ["products-list"],
    queryFn: () => productsApi.list({ per_page: 100 }),
  });

  const allAvatars = avatarsData?.avatars || [];
  const avatars = allAvatars.filter(
    (a: Avatar) => a.status === AvatarStatus.APPROVED || a.status === AvatarStatus.READY
  );
  // Also include non-selectable avatars for display (greyed out)
  const nonSelectableAvatars = allAvatars.filter(
    (a: Avatar) => a.status !== AvatarStatus.APPROVED && a.status !== AvatarStatus.READY && a.status !== AvatarStatus.DRAFT
  );
  const products = productsData?.products || [];

  // Auto-select first avatar
  useEffect(() => {
    if (!selectedAvatar && avatars.length > 0) {
      setSelectedAvatar(avatars[0].id);
    }
  }, [avatars, selectedAvatar]);

  // Pull the picked avatar's existing background looks so the user can
  // pick (or generate) a cast-wide background. We re-run the query when
  // the user switches avatars; clear the picked look since it belongs
  // to the previous avatar.
  // Backend returns { looks: [...] } — unwrap defensively in case the
  // shape ever drifts (e.g. raw array, or no key set).
  const { data: avatarLooksRaw, refetch: refetchLooks } = useQuery<unknown>({
    queryKey: ["avatar-looks-setup", selectedAvatar],
    queryFn: () => avatarLooksApi.list(selectedAvatar!),
    enabled: !!selectedAvatar,
    // Poll while there are non-ready looks so a freshly-generated
    // background appears in the picker the moment it's done. Stops as
    // soon as everything is ready (returns false → no further refetch).
    refetchInterval: (query) => {
      const raw = query.state.data;
      const looks: AvatarLook[] = Array.isArray(raw)
        ? (raw as AvatarLook[])
        : (((raw as { looks?: AvatarLook[] } | undefined)?.looks) ?? []);
      const hasPending = looks.some(
        (l) => l.look_type === "background" && l.status !== "ready" && l.status !== "failed",
      );
      return hasPending ? 5000 : false;
    },
  });
  const avatarLooks: AvatarLook[] = Array.isArray(avatarLooksRaw)
    ? (avatarLooksRaw as AvatarLook[])
    : (((avatarLooksRaw as { looks?: AvatarLook[] } | undefined)?.looks) ?? []);
  const backgroundLooks = avatarLooks.filter(
    (l) => l.look_type === "background" && l.status === "ready",
  );
  // Reset the picked look whenever the avatar changes — a look is owned
  // by exactly one avatar. Only fires on a genuine SWITCH (a real avatar id
  // replaced by a different real avatar id), not on the initial null →
  // value transition from auto-select or from the cast-hydration effect
  // above — otherwise hydrating an existing cast's saved look would get
  // wiped out immediately after being set.
  const prevAvatarForLookResetRef = useRef<string | null>(null);
  useEffect(() => {
    if (prevAvatarForLookResetRef.current && prevAvatarForLookResetRef.current !== selectedAvatar) {
      setSelectedLookId(null);
    }
    prevAvatarForLookResetRef.current = selectedAvatar;
  }, [selectedAvatar]);

  // FIX 4 — Auto-select the first ready background on mount/avatar-change
  // so the left tile in Setup isn't blank. Skipped if the user already
  // picked something (don't override an explicit choice).
  // FIX 10 — When a freshly generated background flips to ready, the
  // refetchInterval above brings it into `backgroundLooks`. The picker
  // already optimistically pre-selects the in-flight look at create-time
  // (AvatarLookPicker.tsx onSuccess), so it'll be highlighted the moment
  // the thumbnail appears.
  useEffect(() => {
    if (backgroundLooks.length > 0 && !selectedLookId) {
      setSelectedLookId(backgroundLooks[0].id);
    }
  }, [backgroundLooks, selectedLookId]);

  const createMutation = useMutation({
    mutationFn: async () => {
      if (cast) {
        // The cast already exists — the user navigated back to Setup (e.g.
        // after AI generated a script) and hit Continue again. Don't call
        // create() a second time, which would spawn a duplicate cast with
        // its own separate outline/script generation run. Instead, patch
        // only the fields that are safe to change post-creation and advance
        // forward with the SAME cast.
        //
        // If any script-shaping field changed since we hydrated (brief,
        // duration, avatar, products, quality, template, cast type,
        // production level, b-roll source), the user came back specifically
        // to rework the script — "Generate Script" should rebuild it after
        // confirming, not silently no-op. Structural fields are only sent
        // (and only accepted by the backend) in that regenerate case, while
        // the cast is still pre-render.
        const currentSnapshot = serializeRegenFields({
          description,
          durationManual,
          durationTarget,
          avatar: selectedAvatar ?? null,
          products: selectedProducts,
          productionLevel,
          castType,
          template: selectedTemplate ?? null,
          brollMediaSource,
        });
        const somethingChanged =
          regenSnapshotRef.current !== null &&
          currentSnapshot !== regenSnapshotRef.current;
        regenRequestedRef.current =
          somethingChanged &&
          (await confirmAction({
            title: "Regenerate script?",
            text:
              "This rebuilds the script from your new settings — the current " +
              "script and any edits to it are replaced, and the voice audio " +
              "is marked stale so you'll re-generate it.",
            confirmButtonText: "Regenerate",
            cancelButtonText: "Keep current",
            icon: "warning",
          }));

        const patched = await castsApi.patch(cast.id, {
          name: castName || undefined,
          description: description || undefined,
          output_format: outputFormat,
          duration_target_seconds: durationManual ? durationTarget : undefined,
          platform_target: targetPlatforms[0] || "tiktok",
          target_platforms: targetPlatforms,
          default_avatar_look_id: selectedLookId || "",
          music_track_choice: musicChoice,
          broll_media_source: brollMediaSource,
          // Render-only — safe to send on every Continue; the next render
          // picks it up. Never triggers a script rebuild.
          quality,
          ...(musicChoice === "custom" && pickedTrack
            ? { background_music_url: pickedTrack.url, background_music_mood: pickedTrack.mood || null }
            : {}),
          ...(regenRequestedRef.current
            ? {
                regen: true,
                avatar_id: selectedAvatar || undefined,
                cast_type: castType,
                production_level: productionLevel,
                template_id: selectedTemplate || "",
                product_ids: selectedProducts,
              }
            : {}),
        } as any);

        if (regenRequestedRef.current) {
          // Keep the baseline in sync so a second Continue without further
          // edits doesn't prompt again.
          regenSnapshotRef.current = currentSnapshot;
          // Rebuild the outline now (smart vs plain, matching Auto Cast) so
          // it reflects the just-patched settings; the caller then runs
          // generateScripts behind the "Generating Script" phase.
          try {
            await (autoCast
              ? castsApi.generateSmartOutline(cast.id)
              : castsApi.generateOutline(cast.id));
            qc.invalidateQueries({ queryKey: ["cast", cast.id] });
          } catch (err) {
            console.error("Setup-triggered outline regen failed:", err);
          }
        }
        return patched;
      }

      const newCast = await castsApi.create({
        name: castName || undefined,
        avatar_id: selectedAvatar!,
        quality,
        output_format: outputFormat,
        cast_type: castType,
        description: description || undefined,
        duration_target_seconds: durationManual ? durationTarget : undefined,
        platform_target: targetPlatforms[0] || "tiktok",
        target_platforms: targetPlatforms,
        product_ids: selectedProducts.length > 0 ? selectedProducts : undefined,
        default_avatar_look_id: selectedLookId || undefined,
        production_level: productionLevel,
        ...(musicChoice === "custom" && pickedTrack
          ? { background_music_url: pickedTrack.url, background_music_mood: pickedTrack.mood || undefined }
          : {}),
        music_track_choice: musicChoice,
        broll_media_source: brollMediaSource,
        // Stage-1 template. Omitted when null (Auto / let AI choose).
        template_id: selectedTemplate || undefined,
        // LIVE-only: user-uploaded b-roll clips to weave between voiceover takes.
        user_video_ids:
          castType === "live" && selectedUserVideos.length > 0
            ? selectedUserVideos.map((v) => v.id)
            : undefined,
        // LIVE-only hints for the backend outline generator. Keys must match
        // what _build_live_defaults_section (engine/cast_generator.py)
        // actually reads (voiceover / broll / max_duration_seconds) — the
        // previous keys here (primary_track/request_user_videos/b_roll_enabled)
        // didn't match anything the backend looked for, so this whole object
        // was silently ignored for every LIVE cast.
        live_mode_defaults:
          castType === "live"
            ? {
                voiceover: true,
                broll: true,
                max_duration_seconds: durationManual ? durationTarget : undefined,
              }
            : undefined,
      } as any);

      // Generate the outline before returning — script generation (kicked
      // off by the caller, CastBuilder's handleCastCreated, which owns the
      // "Generating Script" loading phase) depends on the outline already
      // existing. This used to be a fire-and-forget chain that also called
      // generateScripts itself, racing a SECOND generateScripts call fired
      // by the caller — duplicate work, and no guarantee the outline had
      // finished before either one ran.
      // Auto Cast → LLM designs avatar/PIP split + auto Pexels; manual →
      // plain avatar_speaking blocks the user can edit themselves.
      try {
        await (autoCast
          ? castsApi.generateSmartOutline(newCast.id)
          : castsApi.generateOutline(newCast.id));
        // Smart outline regenerates blocks (fresh IDs) — invalidate so
        // anything already reading this cast's blocks picks up the new ones.
        qc.invalidateQueries({ queryKey: ["cast", newCast.id] });
      } catch (err) {
        console.error("Outline generation failed:", err);
      }

      return newCast;
    },
    onSuccess: (resultCast) => {
      if (cast) {
        toast({
          title: regenRequestedRef.current ? "Regenerating script…" : "Cast updated",
        });
      } else {
        toast({ title: "Cast created!", description: "Generating script..." });
      }
      onCreated(resultCast, !!cast, regenRequestedRef.current);
      regenRequestedRef.current = false;
    },
    onError: (err: any) => {
      toast({ title: "Error", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    },
  });

  const canCreate = selectedAvatar && description.trim() && selectedProducts.length > 0;

  // Duration ceiling and tick marks depend on the cast type. LIVE lifts the
  // cap to 2h and uses the longer mark set; recorded keeps the legacy 720s cap.
  const isLive = castType === "live";
  const durationMaxSeconds = isLive ? LIVE_MAX_SECONDS : RECORDED_MAX_SECONDS;
  const secondMarks = isLive ? DURATION_MARKS_SECONDS_LIVE : DURATION_MARKS_SECONDS;

  // A real three-tier progression instead of "Quick is capped, the other two
  // are identical": Quick caps at the template's low end (cost savings from
  // a shorter render); Standard caps at the template's high end (stays
  // within what this format normally calls for); Premium is the one tier
  // with no cap — it's the only one allowed to run longer than the
  // template's own range. All three numbers match what the backend already
  // uses in _effective_duration_target_seconds, so the slider can never
  // show something looser than what generation will actually enforce.
  const selectedTemplateData = castTemplates?.find((t) => t.id === selectedTemplate);
  const tierDurationCapSeconds = selectedTemplateData?.est_duration_range
    ? productionLevel === "quick"
      ? selectedTemplateData.est_duration_range[0]
      : productionLevel === "standard"
        ? selectedTemplateData.est_duration_range[1]
        : undefined // premium: uncapped
    : undefined;
  const effectiveDurationMaxSeconds = tierDurationCapSeconds
    ? Math.min(durationMaxSeconds, tierDurationCapSeconds)
    : durationMaxSeconds;

  // Snap an existing manual value down the moment it's capped (switching
  // tier or template while a manual duration is already set) — the cap is
  // enforced immediately, not just on the next slider drag.
  useEffect(() => {
    if (tierDurationCapSeconds && durationTarget > tierDurationCapSeconds) {
      setDurationTarget(tierDurationCapSeconds);
    }
  }, [tierDurationCapSeconds]);

  return (
    <>
      {renderInProgress && <RenderLockBanner onCancelRender={onCancelRender} />}
      <div
        className={cn(
          "max-w-5xl mx-auto px-6 pt-5 pb-10 space-y-5 relative transition-opacity",
          renderInProgress && "opacity-60",
        )}
        inert={renderInProgress}
      >
      {/* HERO CHOICE — the first decision of Stage 1. Two big cards make the
          Recorded vs LIVE pick visually unmissable; LIVE pre-configures the
          cast for long-form voiceover + b-roll. Sits ABOVE everything else. */}
      <CastTypeSelector value={castType} onChange={handleCastTypeChange} />

      {/* Compact header row — cast name + delete stay on one line so the page
          can lead with the prompt below. */}
      <div className="flex items-center gap-3">
        <div className="relative group flex-1 min-w-0">
          <label className="text-[11px] text-white/40 mb-1 flex items-center justify-between">
            <span>Cast name <span className="text-white/25">(optional — auto-named if left blank)</span></span>
            {nameEditing && (
              <span className={cn("tabular-nums", castName.length >= CAST_NAME_MAX_LENGTH ? "text-amber-400/80" : "text-white/25")}>
                {castName.length}/{CAST_NAME_MAX_LENGTH}
              </span>
            )}
          </label>
          <Input
            ref={nameInputRef}
            placeholder="Untitled cast"
            value={castName}
            maxLength={CAST_NAME_MAX_LENGTH}
            onChange={(e) => setCastName(e.target.value.slice(0, CAST_NAME_MAX_LENGTH))}
            onFocus={() => setNameEditing(true)}
            onBlur={() => setNameEditing(false)}
            className="bg-transparent border-0 border-b border-white/10 rounded-none px-0 text-lg font-semibold text-white placeholder-white/30 focus-visible:ring-0 focus-visible:border-accent"
            data-testid="cast-name-input"
          />
          {!nameEditing && (
            <button
              onClick={() => {
                setNameEditing(true);
                nameInputRef.current?.focus();
                nameInputRef.current?.select();
              }}
              className="absolute right-1 bottom-1.5 text-white/30 hover:text-white/60 transition-opacity"
              title="Edit cast name"
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* STAGE 1 — Template picker. The user chooses a concrete structural
          format BEFORE writing the brief; "Auto" (default) keeps the legacy
          flow where the AI decides the structure. The pick threads through to
          the cast as template_id and constrains the outline generator. */}
      <TemplateGrid
        templates={castTemplates || []}
        value={selectedTemplate}
        onChange={setSelectedTemplate}
      />

      {/* HERO PROMPT — the page leads with what the user actually thinks
          about: "what do you want this cast to do?". Pure intent input —
          no toggles inside the card so nothing competes with the prompt
          for attention. Smart Cast lives next to Duration below. */}
      <div className="rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] p-5 focus-within:border-accent/50 focus-within:ring-1 focus-within:ring-accent/30 transition-all">
        <div className="mb-3">
          <h3 className="flex items-center gap-2 text-base font-semibold text-white">
            <Wand2 className="w-4 h-4 text-accent" />
            What do you want this cast to do?
            <span className="text-accent">*</span>
          </h3>
          <p className="text-xs text-white/40 mt-0.5">
            The AI uses this to write your script, choose the structure, and pick b-roll.
          </p>
        </div>
        <textarea
          placeholder="e.g. promote this skincare serum to busy moms who don't have time for 10-step routines — hook them fast, show benefits, end with a strong CTA"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={5}
          className="w-full bg-transparent text-base text-white placeholder-white/25 resize-none focus:outline-none leading-relaxed"
          data-testid="cast-description"
        />
      </div>

      {/* Match a past live for THIS cast (stage 1 — optional, non-blocking).
          Mounts as soon as an avatar is selected so the upload is attached to
          the avatar in scope; the resolver still lets cast-level overrides
          win once the cast exists. Lives directly under the brief and above
          the avatar/product/duration controls per the spec. */}
      {selectedAvatar && (
        <div className="rounded-xl border border-white/10 bg-white/5 p-4">
          <LiveReferenceCard
            avatarId={selectedAvatar}
            title="Match a past live (optional)"
            subtitle="Reference a past live so the script matches that style. Optional — your avatar's saved Live Voice is used by default."
          />
        </div>
      )}

      {/* Avatar Picker — compact horizontal strip. */}
      <div className="space-y-2">
        <label className="text-xs font-medium text-white/60 uppercase tracking-wider flex items-center gap-2">
          <UserCircle className="w-3.5 h-3.5" /> Avatar
        </label>
        {avatarsLoading ? (
          <div className="flex items-center gap-2 text-white/50 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading avatars...
          </div>
        ) : avatars.length === 0 && nonSelectableAvatars.length === 0 ? (
          <p className="text-sm text-white/40">No avatars available. Create one in My Avatar first.</p>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1 scroll-smooth">
            {[...avatars, ...nonSelectableAvatars].map((avatar: Avatar) => {
              const selectable = avatar.status === AvatarStatus.APPROVED || avatar.status === AvatarStatus.READY;
              const statusLabel = avatar.status === AvatarStatus.PROCESSING ? "Processing" :
                avatar.status === AvatarStatus.FAILED ? "Failed" : null;
              const isActive = selectable && selectedAvatar === avatar.id;
              return (
                <button
                  key={avatar.id}
                  data-testid="avatar-card"
                  onClick={() => selectable && setSelectedAvatar(avatar.id)}
                  disabled={!selectable}
                  title={avatar.name || "Avatar"}
                  className={cn(
                    "relative shrink-0 w-20 h-20 rounded-xl overflow-hidden border-2 transition",
                    !selectable && "opacity-40 cursor-not-allowed",
                    isActive
                      ? "border-accent ring-2 ring-accent/30"
                      : selectable
                        ? "border-white/10 hover:border-white/30"
                        : "border-white/5"
                  )}
                >
                  {(avatar.face_image_url || avatar.face_ref_key) ? (
                    <img
                      src={avatar.face_image_url || cdnUrl(avatar.face_ref_key!)}
                      alt={avatar.name || "Avatar"}
                      className="w-full h-full object-cover"
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <div className="w-full h-full bg-white/5 flex items-center justify-center">
                      <UserCircle className="w-7 h-7 text-white/30" />
                    </div>
                  )}
                  <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent px-1 py-0.5">
                    <span className="text-[10px] text-white truncate block leading-tight">{avatar.name || "Unnamed"}</span>
                  </div>
                  {statusLabel && (
                    <div className="absolute top-0.5 right-0.5 bg-black/70 rounded px-1 py-0.5">
                      <span className="text-[8px] text-white/60">{statusLabel}</span>
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Avatar Background — thumbnail picker over the avatar's existing
          background looks, with an inline ‘+ Generate new’ form. The
          picked look becomes the cast-wide default; ScriptPhase still
          lets the user override per block. */}
      {selectedAvatar && (
        <div className="space-y-2">
          <label className="text-xs font-medium text-white/60 uppercase tracking-wider flex items-center gap-2">
            <ImageIcon className="w-3.5 h-3.5" /> Scene
          </label>
          <AvatarLookPicker
            avatarId={selectedAvatar}
            looks={backgroundLooks}
            value={selectedLookId}
            onChange={(lookId) => setSelectedLookId(lookId)}
            defaultLabel="Empty scene"
            onLookCreated={() => refetchLooks()}
            size="md"
          />
        </div>
      )}

      {/* Product Picker — compact horizontal strip. */}
      <div className="space-y-2">
        <label className="text-xs font-medium text-white/60 uppercase tracking-wider flex items-center gap-2">
          <Package className="w-3.5 h-3.5" /> Products <span className="text-accent normal-case">*</span>
          {selectedProducts.length > 0 && (
            <span className="text-[10px] text-accent ml-1 normal-case tracking-normal">{selectedProducts.length} selected</span>
          )}
        </label>
        {productsLoading ? (
          <div className="flex items-center gap-2 text-white/50 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading products...
          </div>
        ) : products.length === 0 ? (
          <p className="text-xs text-white/40">No products in library. Add some in Products first.</p>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
            {products.map((product: ProductWithAssets) => {
              const selected = selectedProducts.includes(product.id);
              return (
                <button
                  key={product.id}
                  onClick={() => {
                    setSelectedProducts((prev) =>
                      selected ? prev.filter((id) => id !== product.id) : [...prev, product.id]
                    );
                  }}
                  title={product.name}
                  className={cn(
                    "relative shrink-0 w-20 rounded-xl border-2 overflow-hidden transition",
                    selected
                      ? "border-accent ring-2 ring-accent/30"
                      : "border-white/10 hover:border-white/30"
                  )}
                >
                  {product.cover_image_url || product.cover_image_key ? (
                    <img
                      src={product.cover_image_url || cdnUrl(product.cover_image_key)}
                      alt={product.name}
                      className="w-full aspect-square object-cover"
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <div className="w-full aspect-square bg-white/5 flex items-center justify-center">
                      <Package className="w-5 h-5 text-white/20" />
                    </div>
                  )}
                  <div className="px-1.5 py-1 bg-black/40">
                    <span className="text-[10px] text-white truncate block leading-tight">{product.name}</span>
                    {product.price != null && (
                      <span className="text-[9px] text-white/40">${product.price}</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* B-roll visual source — applies to stock_photo/stock_video (pure
          B-roll) blocks that have a product attached. "Stock" is today's
          default (Pexels search); "AI-generated" instead makes a
          product-only photo/video from the product's own reference photo, so
          the b-roll actually shows the real product instead of a generic
          stock clip. Either way this is just a default — a specific block's
          visual can still be overridden in the Script tab. Always shown
          (products are a required field, so this always applies once
          generation runs) — previously gated on selectedProducts.length,
          which buried it below Background Music and made it look missing
          until a product was picked. */}
      <div className="space-y-2">
        <div className="text-[10px] font-medium uppercase tracking-wider text-white/50 flex items-center gap-1.5">
          <Film className="w-3 h-3" /> B-roll visual source
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {BROLL_SOURCE_OPTIONS.map((opt) => {
            const active = brollMediaSource === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => setBrollMediaSource(opt.value)}
                aria-pressed={active}
                className={cn(
                  "relative rounded-lg border p-3 text-left transition-all",
                  active
                    ? "border-accent bg-accent/10"
                    : "border-white/10 bg-white/[0.02] hover:border-white/25"
                )}
              >
                {active && (
                  <span className="absolute right-2 top-2 flex h-4 w-4 items-center justify-center rounded-full bg-accent text-white">
                    <Check className="h-2.5 w-2.5" strokeWidth={3} />
                  </span>
                )}
                <div className="flex items-center gap-1.5 pr-5">
                  <opt.Icon
                    className={cn("w-3.5 h-3.5 shrink-0", active ? "text-accent" : "text-white/45")}
                  />
                  <span
                    className={cn(
                      "text-xs font-semibold",
                      active ? "text-white" : "text-white/80"
                    )}
                  >
                    {opt.label}
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-snug text-white/45">{opt.desc}</p>
                <p
                  className={cn(
                    "mt-1.5 text-[10px] font-medium",
                    active ? "text-accent/90" : "text-white/35"
                  )}
                >
                  {opt.hint}
                </p>
              </button>
            );
          })}
        </div>
        <p className="text-[10px] text-white/35">
          Default for product b-roll blocks — you can still override any single block in the Script step.
        </p>
      </div>

      {/* LIVE-only — user b-roll upload prompt. Long-form casts weave the
          user's own clips between voiceover takes, so we surface an explicit
          "upload your clips" affordance here. Hidden entirely in recorded
          mode. Reuses the shared UserVideoPickerDialog. */}
      {castType === "live" && (
        <div
          data-testid="live-upload-clips-card"
          className="rounded-2xl border border-accent/20 bg-gradient-to-br from-accent/[0.07] to-transparent p-4 space-y-3"
        >
          <div className="flex items-center justify-between gap-3">
            <h3 className="flex items-center gap-2 text-base font-semibold text-white">
              <Film className="w-4 h-4 text-accent" />
              Upload your clips (optional)
              {selectedUserVideos.length > 0 && (
                <span className="text-[11px] font-medium text-accent normal-case">
                  {selectedUserVideos.length} selected
                </span>
              )}
            </h3>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setVideoPickerOpen(true)}
              className="shrink-0 border-accent/40 text-white hover:bg-accent/10"
              data-testid="live-add-videos-btn"
            >
              Add videos
            </Button>
          </div>
          <p className="text-xs text-white/50 leading-relaxed">
            LIVE casts work best with your own b-roll. Upload short clips now and
            the AI will weave them in between voiceover takes.
          </p>
          {selectedUserVideos.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {selectedUserVideos.map((v) => (
                <span
                  key={v.id}
                  className="inline-flex items-center gap-1.5 rounded-full bg-white/5 border border-white/10 px-2.5 py-1 text-[11px] text-white/80"
                >
                  <Film className="w-3 h-3 text-accent" />
                  <span className="truncate max-w-[140px]">
                    {v.name || v.original_filename}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setSelectedUserVideos((prev) =>
                        prev.filter((x) => x.id !== v.id),
                      )
                    }
                    className="text-white/40 hover:text-white/80"
                    aria-label={`Remove ${v.name || v.original_filename}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* OUTPUT SETTINGS — a single compact box.
          Layout/Platforms on row 1; Quality slider full-width row 2;
          Smart Cast + Duration row 3. The AI plan strip lives just
          below the box when Smart Cast is on. */}
      <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
          {/* Layout */}
          <div className="space-y-1.5">
            <div className="text-[10px] font-medium uppercase tracking-wider text-white/50 flex items-center gap-1.5">
              <Monitor className="w-3 h-3" /> Layout
            </div>
            <div className="grid grid-cols-4 gap-1.5">
              {LAYOUT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handleLayoutChange(opt.value)}
                  title={`${opt.label} — ${opt.desc}`}
                  className={cn(
                    "rounded-lg border p-2 text-center transition-all",
                    outputFormat === opt.value
                      ? "border-accent bg-accent/10"
                      : "border-white/10 bg-white/[0.02] hover:border-white/20"
                  )}
                >
                  <div className="text-base leading-none">{opt.icon}</div>
                  <div className="text-[10px] font-medium text-white/80 mt-0.5">{opt.label}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Platforms */}
          <div className="space-y-1.5">
            <div className="text-[10px] font-medium uppercase tracking-wider text-white/50">
              Platforms
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(PLATFORM_BY_LAYOUT[outputFormat] || []).map((opt) => {
                const active = targetPlatforms.includes(opt.value);
                return (
                  <button
                    key={opt.value}
                    onClick={() => togglePlatform(opt.value)}
                    className={cn(
                      "px-2.5 py-1 rounded-full text-xs font-medium transition",
                      active
                        ? "bg-accent text-white"
                        : "bg-white/5 text-white/50 border border-white/10 hover:border-white/20"
                    )}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Quality slider — ONLY shown in manual mode. In Auto Cast mode
            the AI picks the quality (locked to HD default) and the slider
            would just confuse the picture: "AI is deciding" + "but you
            picked LQ" doesn't add up. Hide it entirely. */}
        {!autoCast && <QualitySlider value={quality} onChange={setQuality} />}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
          {/* Auto Cast — the headline lever. ON = AI picks per-block
              whether to use full-frame InfiniteTalk avatar or small/medium
              MuseTalk PIP. OFF = user sees the LQ↔HQ quality slider and
              gets a plain avatar-only outline they can edit. */}
          <div className="space-y-1.5">
            <div className="text-[10px] font-medium uppercase tracking-wider text-white/50 flex items-center gap-1.5">
              <Sparkles className="w-3 h-3 text-accent" /> Auto Cast
            </div>
            <button
              type="button"
              onClick={() => setAutoCast((v) => !v)}
              aria-pressed={autoCast}
              className={cn(
                "w-full rounded-lg border px-3 py-2.5 flex items-center justify-between gap-3 transition-all text-left",
                autoCast
                  ? "border-accent/40 bg-gradient-to-br from-accent/15 to-accent/[0.04] hover:border-accent/60"
                  : "border-white/10 bg-white/[0.02] hover:border-white/20"
              )}
              data-testid="auto-cast-toggle"
            >
              <div className="min-w-0">
                <div className="text-xs font-semibold text-white leading-tight">
                  {autoCast ? "AI picks avatar vs PIP" : "Manual quality"}
                </div>
                <div className="text-[10px] text-white/50 mt-0.5 truncate">
                  {autoCast
                    ? "Mixes full avatar and small PIP shots automatically."
                    : "Pick LQ ↔ HQ yourself — see the price update live."}
                </div>
              </div>
              <span
                className={cn(
                  "shrink-0 inline-flex h-5 w-9 items-center rounded-full transition-colors",
                  autoCast ? "bg-accent" : "bg-white/15"
                )}
              >
                <span
                  className={cn(
                    "inline-block h-4 w-4 rounded-full bg-white shadow-md transition-transform",
                    autoCast ? "translate-x-4" : "translate-x-0.5"
                  )}
                />
              </span>
            </button>
          </div>

          {/* Duration */}
          <div className="space-y-1.5">
            <div className="text-[10px] font-medium uppercase tracking-wider text-white/50 flex items-center justify-between">
              <span className="flex items-center gap-1.5"><Clock className="w-3 h-3" /> Duration</span>
              {durationManual ? (
                <button
                  onClick={() => setDurationManual(false)}
                  className="text-[10px] text-white/40 hover:text-white/60 normal-case tracking-normal"
                >
                  auto
                </button>
              ) : (
                <button
                  onClick={() => setDurationManual(true)}
                  className="text-[10px] text-accent hover:text-accent/80 normal-case tracking-normal"
                >
                  set manually
                </button>
              )}
            </div>
            {!durationManual ? (
              <div className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2.5 text-xs text-white/50">
                AI picks duration from your description.
              </div>
            ) : (
              <div className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2.5 space-y-2">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={durationUnit === "seconds" ? (isLive ? 60 : 5) : 60}
                    max={effectiveDurationMaxSeconds}
                    step={durationUnit === "seconds" ? 1 : 60}
                    value={durationTarget}
                    onChange={(e) => setDurationTarget(Math.min(Number(e.target.value), effectiveDurationMaxSeconds))}
                    className="flex-1 accent-accent"
                  />
                  <span className="text-sm font-semibold text-white tabular-nums w-12 text-right">
                    {durationUnit === "minutes"
                      ? `${Math.round(durationTarget / 60)}m`
                      : `${durationTarget}s`}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex gap-1">
                    <button
                      onClick={() => { setDurationUnit("seconds"); if (durationTarget > effectiveDurationMaxSeconds) setDurationTarget(effectiveDurationMaxSeconds); }}
                      className={cn(
                        "px-2 py-0.5 rounded text-[10px] font-medium transition",
                        durationUnit === "seconds" ? "bg-white/15 text-white" : "text-white/40"
                      )}
                    >
                      sec
                    </button>
                    {/* Minutes granularity can't represent a Quick/Standard cap
                        — every template's low AND high end is well under 60s,
                        so "1m" would already exceed it. Disabled rather than
                        shown-and-broken. */}
                    <button
                      disabled={!!tierDurationCapSeconds}
                      onClick={() => {
                        setDurationUnit("minutes");
                        setDurationTarget(Math.min(effectiveDurationMaxSeconds, Math.max(60, Math.round(durationTarget / 60) * 60)));
                      }}
                      className={cn(
                        "px-2 py-0.5 rounded text-[10px] font-medium transition",
                        tierDurationCapSeconds
                          ? "text-white/15 cursor-not-allowed"
                          : durationUnit === "minutes" ? "bg-white/15 text-white" : "text-white/40"
                      )}
                      title={tierDurationCapSeconds ? "Not available while this production level's duration cap is active" : undefined}
                    >
                      min
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-0.5 justify-end" data-testid="duration-marks">
                    {(durationUnit === "seconds"
                      ? secondMarks
                      : isLive
                        ? secondMarks.map((s) => s / 60)
                        : DURATION_MARKS_MINUTES
                    ).filter((m) => (durationUnit === "minutes" ? m * 60 : m) <= effectiveDurationMaxSeconds).map((m) => {
                      const val = durationUnit === "minutes" ? m * 60 : m;
                      return (
                        <button
                          key={m}
                          onClick={() => setDurationTarget(val)}
                          className={cn(
                            "text-[10px] px-1.5 py-0.5 rounded",
                            durationTarget === val ? "text-accent font-semibold" : "text-white/30 hover:text-white/50"
                          )}
                        >
                          {durationUnit === "minutes" ? `${m}m` : `${m}s`}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {tierDurationCapSeconds && (
                  <p className="text-[10px] text-amber-400/70">
                    Capped at {tierDurationCapSeconds}s — {productionLevel === "quick"
                      ? "Quick uses fewer render minutes because the cut is shorter."
                      : "Standard stays within this format's normal length — pick Premium for a longer cut."}
                  </p>
                )}
                {estimatedRenderMinutes != null && (
                  <div className="flex items-center justify-between pt-1 border-t border-white/5">
                    <span className="text-[10px] text-white/40">Uses about</span>
                    <span className="text-xs font-semibold text-accent">
                      {estimatedRenderMinutes.toFixed(1)} render min
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Inline warnings (rare — only show when triggered) */}
        {durationManual && durationTarget > 180 && outputFormat === "9:16" && (
          <p className="mt-3 text-[11px] text-amber-400/80 bg-amber-500/10 rounded px-2 py-1.5">
            TikTok max post length varies by account tier. Confirm your TikTok allows longer posts before rendering.
          </p>
        )}
        {durationManual && durationTarget > 3600 && estimatedRenderMinutes != null && (
          <p className="mt-3 text-[11px] text-amber-400/80 bg-amber-500/10 rounded px-2 py-1.5">
            A render this long at {quality.toUpperCase()} uses about {estimatedRenderMinutes.toFixed(0)} render minutes — check your remaining allowance first.
          </p>
        )}
      </div>

      {/* Production Level selector — replaces the legacy AI-Plan chip
          preview. Three cards: Quick / Standard / Premium. The picked
          level is persisted on the cast and constrains the outline
          generator's block-category mix (duration caps, block count,
          avatar-vs-b-roll ratio). Always shown, regardless of Auto Cast —
          it used to be Auto-Cast-only on the theory that the manual LQ↔HQ
          quality slider already covered "spend dial" duty, but Quality
          and Production Level actually control different things (render
          resolution vs. content structure), so hiding this in manual mode
          just meant duration caps/structure kept applying with zero
          visibility or control. */}
      <ProductionLevelSelector value={productionLevel} onChange={setProductionLevel} hasTemplate={!!selectedTemplate} />

      {/* Background music picker — Off / Auto / Specific track. Auto (default)
          generates mood-driven AI music; the renderer places it under the
          narration and ducks it when the voice plays. "Specific track" opens
          a picker over the real ~12K-track library (same one the Music page
          browses) rather than the old 5-item hardcoded placeholder list. */}
      <MusicSelector
        value={musicChoice}
        onChange={setMusicChoice}
        pickedTrack={pickedTrack}
        onOpenPicker={() => setMusicTrackPickerOpen(true)}
      />
      <MusicTrackPickerModal
        open={musicTrackPickerOpen}
        onClose={() => setMusicTrackPickerOpen(false)}
        onSelect={(track) => {
          setPickedTrack({ url: track.url, mood: track.mood, name: track.name });
          setMusicChoice("custom");
          setMusicTrackPickerOpen(false);
        }}
      />

      {/* Sticky-feel Generate footer */}
      <div className="flex items-center justify-between pt-2">
        <p className="text-xs text-white/40">
          {!description.trim()
            ? "Add a description above to enable generation."
            : !selectedAvatar
              ? "Select an avatar to enable generation."
              : selectedProducts.length === 0
                ? "Select at least one product to enable generation."
                : "Ready to write your script."}
        </p>
        <Button
          size="lg"
          disabled={!canCreate || createMutation.isPending}
          onClick={() => createMutation.mutate()}
          className="bg-accent hover:bg-accent/90 disabled:opacity-40"
          data-testid="setup-generate-btn"
          title={
            !description.trim()
              ? "Add a description first"
              : !selectedAvatar
                ? "Select an avatar"
                : selectedProducts.length === 0
                  ? "Select at least one product"
                  : ""
          }
        >
          {createMutation.isPending ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Creating...
            </>
          ) : (
            <>
              <Wand2 className="w-4 h-4 mr-2" /> Generate Script →
            </>
          )}
        </Button>
      </div>

      {/* User b-roll picker — shared single-select dialog reused for multi-
          select: each pick is appended to the LIVE clip list (re-open to add
          more); already-selected clips are skipped. */}
      <UserVideoPickerDialog
        open={videoPickerOpen}
        onClose={() => setVideoPickerOpen(false)}
        onSelect={(video) =>
          setSelectedUserVideos((prev) =>
            prev.some((v) => v.id === video.id) ? prev : [...prev, video],
          )
        }
      />
      </div>
    </>
  );
}


/**
 * CastTypeSelector — the hero choice of Stage 1.
 *
 * A two-card segmented radio control (Recorded vs LIVE) rendered ABOVE
 * everything else. Each card carries a large pictogram, a bold title, and a
 * one-line description. The control is keyboard-accessible: it's a radiogroup
 * of role="radio" cards, focusable with Tab and toggled with Enter/Space, and
 * arrow keys move between the two options.
 *
 * Pictograms:
 *   - Recorded → Film (evokes produced, polished short-form content).
 *   - LIVE     → Radio with a pulsing red dot overlaid (broadcast signal).
 */
function CastTypeSelector({
  value,
  onChange,
}: {
  value: "recorded" | "live";
  onChange: (v: "recorded" | "live") => void;
}) {
  const OPTIONS = [
    {
      id: "recorded" as const,
      title: "Recorded",
      desc: "Polished short-form video.",
      icon: Film,
    },
    {
      id: "live" as const,
      title: "LIVE",
      desc: "Long-form host-style content.",
      icon: Radio,
    },
  ];

  const handleKeyDown = (e: React.KeyboardEvent, id: "recorded" | "live") => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onChange(id);
    } else if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      onChange("live");
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      onChange("recorded");
    }
  };

  return (
    <div className="space-y-2">
      <div
        role="radiogroup"
        aria-label="Cast type"
        data-testid="cast-type-selector"
        className="grid grid-cols-1 sm:grid-cols-2 gap-3"
      >
        {OPTIONS.map((opt) => {
          const Icon = opt.icon;
          const active = value === opt.id;
          const isLive = opt.id === "live";
          return (
            <div
              key={opt.id}
              role="radio"
              aria-checked={active}
              tabIndex={0}
              data-testid={`cast-type-${opt.id}`}
              onClick={() => onChange(opt.id)}
              onKeyDown={(e) => handleKeyDown(e, opt.id)}
              className={cn(
                "relative flex flex-col items-center justify-center gap-2 rounded-2xl border-2 px-4 py-5 text-center cursor-pointer select-none min-h-[120px] sm:min-h-[140px] transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                active
                  ? "border-accent bg-gradient-to-br from-accent/15 to-accent/[0.04] ring-1 ring-accent/40 shadow-[0_0_24px_-6px_rgba(168,85,247,0.55)]"
                  : "border-white/10 bg-white/[0.02] hover:border-white/25",
              )}
            >
              <div className="relative">
                <Icon
                  className={cn(
                    "w-12 h-12 sm:w-14 sm:h-14 transition-opacity duration-200",
                    active ? "text-accent opacity-100" : "text-white/60 opacity-70",
                  )}
                />
                {/* Pulsing red dot — broadcast signal for the LIVE card. */}
                {isLive && (
                  <span className="absolute -top-0.5 -right-0.5 flex h-3 w-3">
                    <span className="absolute inline-flex h-full w-full rounded-full bg-red-500 opacity-75 animate-ping" />
                    <span className="relative inline-flex h-3 w-3 rounded-full bg-red-500 animate-pulse" />
                  </span>
                )}
              </div>
              <div
                className={cn(
                  "text-base sm:text-lg font-bold leading-tight",
                  active ? "text-white" : "text-white/80",
                )}
              >
                {opt.title}
              </div>
              <div className="text-xs text-white/45 leading-snug">{opt.desc}</div>
            </div>
          );
        })}
      </div>
      {/* Subtle helper copy under the toggle — switches with the mode. */}
      <p className="text-xs text-white/45 text-center" data-testid="cast-type-helper">
        {value === "recorded"
          ? "Polished short-form video. Best for hooks, ads, and TikTok-ready clips."
          : "Long-form host-style content. Voiceover + your b-roll clips. Up to 2 hours."}
      </p>
    </div>
  );
}


/**
 * TemplateGrid — Stage-1 creative template picker.
 *
 * Shows a responsive grid of concrete structural formats (Talking Head Hook,
 * Demo Heavy, …) plus an "Auto / Let AI choose" card that is selected by
 * default. Picking a template sets `template_id` on the cast; "Auto" leaves it
 * null so the outline generator decides the structure freely (legacy flow).
 *
 * The card highlights when selected. Each card shows an icon, name, one-line
 * description, and a small meta line (block count + est. duration).
 */
function TemplateGrid({
  templates,
  value,
  onChange,
}: {
  templates: CastTemplate[];
  value: string | null;
  onChange: (id: string | null) => void;
}) {
  return (
    <div className="rounded-2xl border border-accent/15 bg-gradient-to-br from-accent/[0.06] to-transparent px-4 py-3 space-y-3">
      <div>
        <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-accent/80 flex items-center gap-1.5">
          <Layers className="w-3 h-3" /> Template
        </h3>
        <p className="text-[11px] text-white/40 mt-0.5">
          Pick a structure to start from, or let the AI choose for you.
        </p>
      </div>
      <div
        className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2.5"
        data-testid="template-grid"
      >
        {/* Auto card — default. Clears the template pick. */}
        <button
          type="button"
          onClick={() => onChange(null)}
          aria-pressed={value === null}
          data-testid="template-card-auto"
          className={cn(
            "p-3 rounded-xl border text-left transition-all flex flex-col gap-1.5",
            value === null
              ? "bg-accent/10 border-accent/40 ring-1 ring-accent/30"
              : "bg-white/[0.03] border-white/[0.07] hover:border-white/20",
          )}
        >
          <Sparkles className="w-4 h-4 text-accent" />
          <div className="text-sm font-medium text-white/90 leading-tight">Auto</div>
          <div className="text-[10px] text-white/40 leading-snug">
            Let AI choose the structure for you.
          </div>
        </button>

        {templates.map((tpl) => {
          const Icon = TEMPLATE_ICONS[tpl.id] || Film;
          const active = value === tpl.id;
          const [lo, hi] = tpl.est_duration_range || [];
          return (
            <button
              key={tpl.id}
              type="button"
              onClick={() => onChange(tpl.id)}
              aria-pressed={active}
              data-testid={`template-card-${tpl.id}`}
              title={tpl.description}
              className={cn(
                "p-3 rounded-xl border text-left transition-all flex flex-col gap-1.5",
                active
                  ? "bg-accent/10 border-accent/40 ring-1 ring-accent/30"
                  : "bg-white/[0.03] border-white/[0.07] hover:border-white/20",
              )}
            >
              <Icon className="w-4 h-4 text-white/70" />
              <div className="text-sm font-medium text-white/90 leading-tight">{tpl.name}</div>
              <div className="text-[10px] text-white/40 leading-snug line-clamp-2">
                {tpl.description}
              </div>
              <div className="text-[9px] text-white/30 mt-auto pt-1">
                {tpl.block_count} blocks
                {lo != null && hi != null ? ` · ${lo}–${hi}s` : ""}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * ProductionLevelSelector — three-card picker for the production budget.
 *
 * Replaces the legacy AI-Plan chip preview. The user picks one of three
 * tiers, applied ON TOP of whichever Template is selected above — it never
 * overrides the template's identity, only how lean/full a cut of it you get:
 *   - Quick:    the leanest structurally-valid cut — repeated beats
 *               collapsed to one, b-roll trimmed toward the template's low
 *               end, shortest duration in the template's range.
 *   - Standard: the template's natural block count, bias, and duration —
 *               unchanged from today's behavior.
 *   - Premium:  one extra beat, b-roll pushed toward the template's high
 *               end, longest duration in the template's range.
 *
 * This is a no-op with no Template selected (Auto mode) — there's no
 * per-template data to derive a lean/full cut from, same as how the
 * Template constraint itself only applies once one is picked.
 *
 * Billing note shown per card is the render-MINUTE multiplier (renders are
 * metered in minutes, not dollars). Quick and Standard bill at the same
 * ×1.0 rate — Quick only uses fewer minutes because it produces a shorter
 * video. Premium is ×1.5.
 */
function ProductionLevelSelector({
  value,
  onChange,
  hasTemplate,
}: {
  value: "quick" | "standard" | "premium";
  onChange: (v: "quick" | "standard" | "premium") => void;
  hasTemplate: boolean;
}) {
  const LEVELS = [
    {
      id: "quick" as const,
      name: "Quick",
      icon: "⚡",
      desc: hasTemplate
        ? "Fewer beats (no repeats), mostly talking-head, shortest cut of your chosen format."
        : "Fewer, shorter beats — a lean ~35s talking-head cut.",
      minutes: "×1.0 render minutes · shorter, so fewer used",
    },
    {
      id: "standard" as const,
      name: "Standard",
      icon: "✦",
      desc: hasTemplate
        ? "Your template's natural beat count, shot mix, and length — unchanged."
        : "Balanced length and shot mix — whatever the script calls for.",
      minutes: "×1.0 render minutes",
    },
    {
      id: "premium" as const,
      name: "Premium",
      icon: "★",
      desc: hasTemplate
        ? "One extra beat, more b-roll, longest cut of your chosen format."
        : "Longer and richer — more beats and more b-roll.",
      minutes: "×1.5 render minutes",
    },
  ];
  
  return (
    <div className="rounded-2xl border border-accent/15 bg-gradient-to-br from-accent/[0.06] to-transparent px-4 py-3 space-y-3">
      <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-accent/80 flex items-center gap-1.5">
        <Sparkles className="w-3 h-3" /> Production Level
      </h3>
      <div className="grid grid-cols-3 gap-3">
        {LEVELS.map((level) => {
          const active = value === level.id;
          return (
            <button
              key={level.id}
              type="button"
              onClick={() => onChange(level.id)}
              className={cn(
                "p-4 rounded-xl border text-left transition-all",
                active
                  ? "bg-accent/10 border-accent/40 ring-1 ring-accent/30"
                  : "bg-white/[0.03] border-white/[0.07] hover:border-white/20",
              )}
            >
              <div className="text-lg mb-1">{level.icon}</div>
              <div className="text-sm font-medium text-white/90">{level.name}</div>
              <div className="text-[10px] text-white/40 mt-1 leading-snug">{level.desc}</div>
              <div className="text-[10px] text-accent/80 mt-2">{level.minutes}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * MusicSelector — Off / Auto / Specific-track picker for background music.
 *
 * The choice is persisted on the cast as `music_track_choice`:
 *   - "off"            no background music (skips AI generation entirely)
 *   - "auto"           mood-driven AI music (default, recommended)
 *   - "custom"         a real library track, chosen via MusicTrackPickerModal
 *   - "track_id:<id>"  legacy — one of the old 5 hardcoded placeholder
 *                      tracks; no longer offered as a new choice, but old
 *                      casts that already have one keep resolving correctly.
 * The renderer auto-places the chosen track on the timeline under the
 * narration and ducks it when the voice plays.
 */
function MusicSelector({
  value,
  onChange,
  pickedTrack,
  onOpenPicker,
}: {
  value: string;
  onChange: (v: string) => void;
  pickedTrack: { url: string; mood: string; name: string } | null;
  onOpenPicker: () => void;
}) {
  const isSpecificTrack = value === "custom" || value.startsWith("track_id:");
  const MODES = [
    { id: "off", name: "Off", desc: "No background music." },
    { id: "auto", name: "Auto", desc: "AI music tuned to your script.", recommended: true },
    { id: "track", name: "Specific track", desc: "Pick from the library." },
  ] as const;
  const activeMode = value === "off" ? "off" : isSpecificTrack ? "track" : "auto";

  return (
    <div className="rounded-2xl border border-accent/15 bg-gradient-to-br from-accent/[0.06] to-transparent px-4 py-3 space-y-3">
      <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-accent/80 flex items-center gap-1.5">
        <Sparkles className="w-3 h-3" /> Background Music
      </h3>
      <div className="grid grid-cols-3 gap-3">
        {MODES.map((mode) => {
          const active = activeMode === mode.id;
          return (
            <button
              key={mode.id}
              type="button"
              data-testid={`music-mode-${mode.id}`}
              onClick={() => {
                if (mode.id === "off") onChange("off");
                else if (mode.id === "auto") onChange("auto");
                else onOpenPicker();
              }}
              className={cn(
                "p-4 rounded-xl border text-left transition-all",
                active
                  ? "bg-accent/10 border-accent/40 ring-1 ring-accent/30"
                  : "bg-white/[0.03] border-white/[0.07] hover:border-white/20",
              )}
            >
              <div className="text-sm font-medium text-white/90">
                {mode.name}
                {"recommended" in mode && mode.recommended && (
                  <span className="ml-1 text-[9px] text-accent">(recommended)</span>
                )}
              </div>
              <div className="text-[10px] text-white/40 mt-1 leading-snug">{mode.desc}</div>
            </button>
          );
        })}
      </div>
      
      {activeMode === "track" && (
        <div className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.05] border border-white/10 px-3 py-2.5">
          <div className="min-w-0">
            <div className="text-sm text-white/90 truncate">
              {pickedTrack?.name || (value.startsWith("track_id:") ? "Library track" : "No track picked yet")}
            </div>
            {pickedTrack?.mood && (
              <div className="text-[10px] text-white/40 truncate">{pickedTrack.mood}</div>
            )}
          </div>
          <button
            type="button"
            onClick={onOpenPicker}
            className="shrink-0 rounded-md border border-white/15 bg-white/5 hover:bg-white/10 px-3 py-1.5 text-xs text-white/80 transition-colors"
          >
            {pickedTrack ? "Change" : "Browse library"}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * QualitySlider — a 3-stop horizontal track styled like a hi-fi VU meter.
 *
 * The track itself is a gradient (cool → warm) so the visual hint matches
 * the user's mental model of "more = pricier and prettier". The thumb is
 * an accent-glowing puck the user can DRAG along the track — it snaps to
 * the nearest stop on release. Clicking anywhere on the track also moves
 * the thumb there. Keyboard arrow keys nudge between stops.
 *
 * Why pointer events instead of <input type="range">: the range input
 * doesn't let us style a 3-stop track this way without per-browser
 * gymnastics, and we need the discrete snap-to-stop behavior with the
 * gradient fill. Pointer Events handle mouse + touch + pen uniformly.
 */
function QualitySlider({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const STOPS = [
    { value: "simple", short: "LQ", label: "Lite", mult: "1.0× minutes", desc: "Fast generation" },
    { value: "hd", short: "HQ", label: "HD", mult: "1.4× minutes", desc: "High quality" },
    { value: "hd_plus", short: "HQ+", label: "HD+", mult: "2.0× minutes", desc: "Premium quality" },
  ];
  const idx = Math.max(0, STOPS.findIndex((s) => s.value === value));
  const pct = (idx / (STOPS.length - 1)) * 100;
  const active = STOPS[idx];

  // Track DOM ref + transient drag state. While dragging we update a
  // local `dragPct` so the thumb follows the cursor smoothly; on release
  // we snap to the nearest stop and call onChange.
  const trackRef = useRef<HTMLDivElement>(null);
  const [dragPct, setDragPct] = useState<number | null>(null);
  const dragging = dragPct !== null;

  const stopFromPct = useCallback((p: number) => {
    // Snap a 0-100 percentage to the nearest stop index.
    const clamped = Math.max(0, Math.min(100, p));
    return Math.round((clamped / 100) * (STOPS.length - 1));
  }, [STOPS.length]);

  const pctFromClientX = useCallback((clientX: number) => {
    const el = trackRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    return ((clientX - rect.left) / rect.width) * 100;
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
    const p = pctFromClientX(e.clientX);
    setDragPct(p);
  }, [pctFromClientX]);

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    setDragPct(pctFromClientX(e.clientX));
  }, [dragging, pctFromClientX]);

  const handlePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const finalPct = pctFromClientX(e.clientX);
    const newIdx = stopFromPct(finalPct);
    setDragPct(null);
    if (newIdx !== idx) onChange(STOPS[newIdx].value);
    try { (e.currentTarget as HTMLDivElement).releasePointerCapture(e.pointerId); } catch { /* ignore */ }
  }, [dragging, idx, onChange, pctFromClientX, stopFromPct, STOPS]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
      e.preventDefault();
      const next = Math.max(0, idx - 1);
      if (next !== idx) onChange(STOPS[next].value);
    } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = Math.min(STOPS.length - 1, idx + 1);
      if (next !== idx) onChange(STOPS[next].value);
    } else if (e.key === "Home") {
      e.preventDefault();
      if (idx !== 0) onChange(STOPS[0].value);
    } else if (e.key === "End") {
      e.preventDefault();
      const last = STOPS.length - 1;
      if (idx !== last) onChange(STOPS[last].value);
    }
  }, [idx, onChange, STOPS]);

  // Visible thumb position: while dragging follow the cursor, otherwise
  // snap to the active stop.
  const visualPct = dragPct !== null ? Math.max(0, Math.min(100, dragPct)) : pct;
  const previewIdx = dragPct !== null ? stopFromPct(dragPct) : idx;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <div className="text-[10px] font-medium uppercase tracking-wider text-white/50">Quality</div>
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] text-white/40">{STOPS[previewIdx].desc}</span>
          <span className="text-xs font-semibold text-accent tabular-nums">{STOPS[previewIdx].mult}</span>
        </div>
      </div>

      <div
        ref={trackRef}
        role="slider"
        aria-valuemin={0}
        aria-valuemax={STOPS.length - 1}
        aria-valuenow={idx}
        aria-valuetext={active.label}
        tabIndex={0}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onKeyDown={handleKeyDown}
        className={cn(
          "relative h-9 select-none touch-none cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 rounded-full",
          dragging && "cursor-grabbing"
        )}
        data-testid="quality-slider"
      >
        {/* Track */}
        <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 h-1.5 rounded-full bg-gradient-to-r from-sky-400/30 via-violet-400/30 to-fuchsia-400/30" />
        {/* Filled portion follows the thumb */}
        <div
          className={cn(
            "pointer-events-none absolute top-1/2 -translate-y-1/2 h-1.5 rounded-full bg-gradient-to-r from-sky-400 via-violet-400 to-fuchsia-400",
            !dragging && "transition-[width] duration-200"
          )}
          style={{ width: `${visualPct}%` }}
        />
        {/* Stop tick marks (visual only, not buttons — the parent track
            owns the pointer events so dragging works seamlessly across
            the whole bar). */}
        {STOPS.map((_, i) => {
          const stopPct = (i / (STOPS.length - 1)) * 100;
          const isActive = i === previewIdx;
          return (
            <span
              key={i}
              aria-hidden="true"
              className="pointer-events-none absolute top-1/2 -translate-y-1/2 -translate-x-1/2"
              style={{ left: `${stopPct}%` }}
            >
              <span
                className={cn(
                  "block rounded-full transition-[height,width,background-color] duration-150",
                  isActive ? "h-1 w-1 bg-white/0" : "h-1.5 w-1.5 bg-white/30"
                )}
              />
            </span>
          );
        })}
        {/* Draggable thumb — sits at the visual position. */}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 -translate-y-1/2 -translate-x-1/2"
          style={{ left: `${visualPct}%` }}
        >
          <span
            className={cn(
              "block h-5 w-5 rounded-full bg-white transition-shadow",
              dragging
                ? "shadow-[0_0_0_4px_rgba(255,255,255,0.18),0_0_28px_6px_rgba(168,85,247,0.7)]"
                : "shadow-[0_0_0_3px_rgba(255,255,255,0.15),0_0_24px_4px_rgba(168,85,247,0.55)]"
            )}
          />
        </span>
      </div>

      {/* Stop labels — still clickable for users who'd rather tap a stop
          than drag. We stop propagation so clicking a label doesn't also
          fire the track's pointer-down handler. */}
      <div className="relative h-4">
        {STOPS.map((s, i) => {
          const stopPct = (i / (STOPS.length - 1)) * 100;
          const isActive = i === previewIdx;
          return (
            <button
              key={s.value}
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => onChange(s.value)}
              className={cn(
                "absolute top-0 -translate-x-1/2 text-[10px] font-semibold tracking-wider transition-colors",
                isActive ? "text-accent" : "text-white/40 hover:text-white/70"
              )}
              style={{ left: `${stopPct}%` }}
            >
              {s.short}
            </button>
          );
        })}
      </div>
    </div>
  );
}
