import { useState, useEffect, useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Loader2, GripVertical, Trash2, Plus, Wand2, Volume2, RefreshCw, FileText,
  Check, ChevronDown, MessageSquare, Undo2, Redo2, Captions, Send,
  Type, Scissors, Smartphone, X, Sparkles,
} from "lucide-react";
import { CAPTION_PRESETS, getCaptionPreset, DEFAULT_CAPTION_PRESET_ID } from "@/lib/captionPresets";
import { Button } from "@/components/ui/button";
import { castsApi, scriptApi, avatarApi, avatarLooksApi } from "@/lib/api";
import { FrameSlot } from "@/components/cast-builder/FrameSlot";
import { ParallelMediaPicker } from "@/components/cast-builder/ParallelMediaPicker";
import { ProductCarouselToggle } from "@/components/cast-builder/scriptphase/ProductCarouselToggle";
import { VisualSourcePicker } from "@/components/cast-builder/scriptphase/VisualSourcePicker";
import { BlockProductPicker } from "@/components/cast-builder/scriptphase/BlockProductPicker";
import { AvatarLookPicker } from "@/components/cast-builder/scriptphase/AvatarLookPicker";
import { BlockVisualPreview } from "@/components/cast-builder/scriptphase/BlockVisualPreview";
import { ProsodyHighlighter, stripProsody } from "@/components/cast-builder/scriptphase/ProsodyHighlighter";
import { BlockTimeline } from "@/components/cast-builder/scriptphase/BlockTimeline";
import { RefineInput } from "@/components/cast-builder/scriptphase/RefineInput";
import { BlockType, BlockCategory, type Cast, type Block, type Variant, type AvatarLook } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { BLOCK_CATEGORIES, CATEGORY_MAP, getCategoryInfo } from "@/lib/blockCategories";
import { ActionFrameCarousel } from "@/components/cast-builder/ActionFrameCarousel";
import { ClipMicToggle } from "@/components/avatar/ClipMicToggle";
import { LiveReferenceCard } from "@/components/avatar/LiveReferenceCard";
import { RenderLockBanner } from "@/components/cast-builder/RenderLockBanner";

interface ScriptPhaseProps {
  cast: Cast;
  onDone: (cast: Cast) => void;
  // True while a render is actively queued/baking/composing for this cast.
  // The render task reads live script text/voice per block as it bakes, so
  // an edit here mid-render can leave already-baked blocks on the old
  // script/voice while later blocks pick up the new one — lock editing
  // while this is true instead of letting that race happen silently.
  renderInProgress?: boolean;
  onCancelRender?: () => void;
}

// ── 4.7.9 — Word count: TTS-bound text only, excluding [gesture:] markers ──
function ttsWordCount(text: string): number {
  // Remove [gesture:...] markers before counting
  const clean = text.replace(/\[gesture:[^\]]*\]/g, "").trim();
  return clean.split(/\s+/).filter(Boolean).length;
}

function estimateDuration(text: string): number {
  return Math.round((ttsWordCount(text) / 2.5) * 10) / 10;
}

// ── 6.6.1 — Valid gesture thesaurus for validation ──
const VALID_GESTURES = new Set([
  "point", "point_up", "point_down", "point_left", "point_right", "this", "here",
  "nod", "thumbs_up", "ok", "clap", "yes",
  "shake_head", "no", "dismiss", "stop",
  "wave", "hello", "bye", "bow", "salute",
  "big", "small", "huge", "tiny", "explode", "mind_blown", "fire", "celebrate",
  "count", "one", "two", "three", "first", "second",
  "shrug", "think", "wonder", "confused", "hmm",
  "present", "show", "look", "listen", "reveal", "compare",
  "heart", "love", "surprise", "laugh", "cry", "proud",
  "money", "expensive", "cheap", "deal", "save",
  "come", "go", "wait", "hurry", "slow", "power",
  "scroll", "click", "swipe", "phone", "camera", "type",
  "eat", "drink", "chef_kiss", "taste",
  "but", "also", "finally", "next", "back", "remember",
]);

// ── 6.6.2 — Levenshtein distance for gesture suggestions ──
function levenshtein(a: string, b: string): number {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = Math.min(
        dp[i-1][j] + 1, dp[i][j-1] + 1,
        dp[i-1][j-1] + (a[i-1] !== b[j-1] ? 1 : 0),
      );
  return dp[m][n];
}

function suggestGestures(input: string, maxResults = 3): string[] {
  const all = Array.from(VALID_GESTURES);
  return all
    .map(g => ({ g, d: levenshtein(input.toLowerCase(), g.toLowerCase()) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, maxResults)
    .filter(x => x.d <= Math.max(3, Math.floor(input.length / 2)))
    .map(x => x.g);
}

// ── 6.6.1 — GestureHighlightedText: renders script text with validated gesture markers ──
function GestureHighlightedText({ text }: { text: string }) {
  const GESTURE_RE = /(\[gesture:([^\]]*)\])/g;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = GESTURE_RE.exec(text)) !== null) {
    // Text before the marker
    if (match.index > lastIndex) {
      parts.push(<span key={`t-${lastIndex}`}>{text.slice(lastIndex, match.index)}</span>);
    }
    const fullMatch = match[1];
    const gestureName = match[2].trim();
    const isValid = VALID_GESTURES.has(gestureName);

    if (isValid) {
      parts.push(
        <span
          key={`g-${match.index}`}
          className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-accent/20 text-accent mx-0.5"
        >
          {fullMatch}
        </span>
      );
    } else {
      const suggestions = suggestGestures(gestureName);
      const tooltipText = suggestions.length > 0
        ? `"${gestureName}" is not a known gesture. Did you mean: ${suggestions.join(", ")}?`
        : `"${gestureName}" is not a known gesture.`;
      parts.push(
        <GestureInvalidMarker key={`g-${match.index}`} text={fullMatch} tooltip={tooltipText} />
      );
    }
    lastIndex = match.index + fullMatch.length;
  }

  if (lastIndex < text.length) {
    parts.push(<span key={`t-${lastIndex}`}>{text.slice(lastIndex)}</span>);
  }

  return <div className="text-sm text-white/70 whitespace-pre-wrap leading-relaxed px-3 py-2">{parts}</div>;
}

// ── 6.6.2 — Invalid gesture marker with hover tooltip ──
function GestureInvalidMarker({ text, tooltip }: { text: string; tooltip: string }) {
  const [show, setShow] = useState(false);
  return (
    <span
      className="relative inline-flex items-center cursor-help"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <span className="underline decoration-red-500 decoration-wavy decoration-2 text-red-400/80 mx-0.5">
        {text}
      </span>
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 rounded-lg bg-zinc-800 border border-white/10 text-[11px] text-white/90 shadow-xl whitespace-nowrap z-50 pointer-events-none">
          {tooltip}
          <span className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0 border-x-4 border-x-transparent border-t-4 border-t-zinc-800" />
        </span>
      )}
    </span>
  );
}

// ── 6.6.3 — GestureAutocomplete: dropdown on [gesture: trigger ──
const ALL_GESTURES = Array.from(VALID_GESTURES).sort();

function GestureAutocomplete({
  textareaRef,
  text,
  onInsert,
}: {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  text: string;
  onInsert: (replacement: string, start: number, end: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const [triggerStart, setTriggerStart] = useState(-1);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = filter
    ? ALL_GESTURES.filter(g => g.startsWith(filter.toLowerCase()))
    : ALL_GESTURES;

  const checkTrigger = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    const cursor = el.selectionStart;
    const before = el.value.slice(0, cursor);
    // Look for "[gesture:" pattern before cursor that hasn't been closed yet
    const triggerMatch = before.match(/\[gesture:([^\]\[]*)$/);
    if (triggerMatch) {
      setOpen(true);
      setFilter(triggerMatch[1]);
      setTriggerStart(cursor - triggerMatch[0].length);
      setSelectedIdx(0);

      // Approximate position based on textarea and cursor
      const linesBefore = before.split("\n");
      const lineIdx = linesBefore.length - 1;
      const colIdx = linesBefore[lineIdx].length;
      const lineHeight = 20;
      const charWidth = 7.5;
      setPosition({
        top: (lineIdx + 1) * lineHeight + 8,
        left: Math.min(colIdx * charWidth, el.offsetWidth - 200),
      });
    } else {
      setOpen(false);
      setTriggerStart(-1);
    }
  }, [textareaRef]);

  useEffect(() => {
    checkTrigger();
  }, [text, checkTrigger]);

  const handleSelect = useCallback((gesture: string) => {
    const el = textareaRef.current;
    if (!el || triggerStart < 0) return;
    const cursor = el.selectionStart;
    const replacement = `[gesture:${gesture}]`;
    onInsert(replacement, triggerStart, cursor);
    setOpen(false);
  }, [textareaRef, triggerStart, onInsert]);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (!open || filtered.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIdx(prev => Math.min(prev + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIdx(prev => Math.max(prev - 1, 0));
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      handleSelect(filtered[selectedIdx]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }, [open, filtered, selectedIdx, handleSelect]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.addEventListener("keydown", handleKeyDown);
    return () => el.removeEventListener("keydown", handleKeyDown);
  }, [textareaRef, handleKeyDown]);

  // Scroll selected item into view
  useEffect(() => {
    if (listRef.current) {
      const item = listRef.current.children[selectedIdx] as HTMLElement | undefined;
      item?.scrollIntoView({ block: "nearest" });
    }
  }, [selectedIdx]);

  if (!open || filtered.length === 0) return null;

  return (
    <div
      className="absolute z-50 bg-zinc-900 border border-white/10 rounded-lg shadow-xl overflow-hidden"
      style={{ top: position.top, left: position.left, maxWidth: 240 }}
    >
      <div ref={listRef} className="max-h-48 overflow-y-auto py-1">
        {filtered.slice(0, 30).map((g, i) => (
          <button
            key={g}
            onMouseDown={e => { e.preventDefault(); handleSelect(g); }}
            className={cn(
              "w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-white/10",
              i === selectedIdx && "bg-accent/20 text-accent"
            )}
          >
            <span className="font-mono text-[11px]">{g}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Per-block undo/redo history (4.7.4) ──
interface HistoryEntry { text: string; category: string; }
const MAX_HISTORY = 20;

function useBlockHistory() {
  const histRef = useRef<Record<string, { stack: HistoryEntry[]; idx: number }>>({});

  const push = useCallback((blockId: string, entry: HistoryEntry) => {
    if (!histRef.current[blockId]) histRef.current[blockId] = { stack: [], idx: -1 };
    const h = histRef.current[blockId];
    // Trim future if we're in the middle of history
    h.stack = h.stack.slice(0, h.idx + 1);
    h.stack.push(entry);
    if (h.stack.length > MAX_HISTORY) h.stack.shift();
    h.idx = h.stack.length - 1;
  }, []);

  const undo = useCallback((blockId: string): HistoryEntry | null => {
    const h = histRef.current[blockId];
    if (!h || h.idx <= 0) return null;
    h.idx--;
    return h.stack[h.idx];
  }, []);

  const redo = useCallback((blockId: string): HistoryEntry | null => {
    const h = histRef.current[blockId];
    if (!h || h.idx >= h.stack.length - 1) return null;
    h.idx++;
    return h.stack[h.idx];
  }, []);

  const canUndo = useCallback((blockId: string) => {
    const h = histRef.current[blockId];
    return h ? h.idx > 0 : false;
  }, []);

  const canRedo = useCallback((blockId: string) => {
    const h = histRef.current[blockId];
    return h ? h.idx < h.stack.length - 1 : false;
  }, []);

  return { push, undo, redo, canUndo, canRedo };
}

export function ScriptPhase({ cast, onDone, renderInProgress, onCancelRender }: ScriptPhaseProps) {
  const queryClient = useQueryClient();
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [generating, setGenerating] = useState(false);
  // True once this component has seen at least one block — lets the empty
  // state tell "you deleted every block" apart from "generation failed / is
  // still loading" (same blocks.length === 0, very different messages).
  const blocksEverLoadedRef = useRef(false);
  // Drag-and-drop state. We split into TWO indices so the visual feedback
  // is rich without mutating the list mid-drag:
  //   dragSrcIdx: the block being dragged (renders semi-transparent + scaled)
  //   dropTargetIdx: where it would land if released NOW (renders an indicator
  //     line + slides neighbors aside)
  // We only commit the reorder on drop, so the user sees a smooth animation
  // and there's no flicker if they drag back over the original position.
  const [dragSrcIdx, setDragSrcIdx] = useState<number | null>(null);
  const [dropTargetIdx, setDropTargetIdx] = useState<number | null>(null);
  const saveTimers = useRef<Record<string, NodeJS.Timeout>>({});
  const [pendingSaves, setPendingSaves] = useState<Set<string>>(new Set());
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());

  // Fetch all looks for the cast's avatar in one query, then split. We
  // need backgrounds (for the per-block dropdown + the new acting first/
  // last fields use body_motion looks). Single query → cheaper, single
  // place to invalidate when a new look is generated.
  const { data: allLooksRaw, refetch: refetchLooks } = useQuery<unknown>({
    queryKey: ["avatar-looks-all", cast.avatar_id],
    queryFn: () => avatarLooksApi.list(cast.avatar_id),
    enabled: !!cast.avatar_id,
  });
  // Backend returns { looks: [...] } — unwrap defensively.
  const allLooks: AvatarLook[] = Array.isArray(allLooksRaw)
    ? (allLooksRaw as AvatarLook[])
    : (((allLooksRaw as { looks?: AvatarLook[] } | undefined)?.looks) ?? []);
  const backgroundLooks = allLooks.filter(l => l.look_type === "background" && l.status === "ready");
  const bodyMotionLooks = allLooks.filter(l => l.look_type === "body_motion" && l.status === "ready");

  // Pull the avatar so each block's visual preview can show the avatar's
  // face on speaking / PIP shots. Cached cast-wide — the cast.avatar_id
  // is stable, so this fires once per session.
  const { data: castAvatar } = useQuery({
    queryKey: ["avatar-status", cast.avatar_id],
    queryFn: () => avatarApi.status(cast.avatar_id),
    enabled: !!cast.avatar_id,
    staleTime: 5 * 60_000,
  });
  const avatarFaceUrl = castAvatar
    ? (castAvatar.face_image_url || (castAvatar.face_ref_key ? cdnUrl(castAvatar.face_ref_key) : null))
    : null;

  // Auto-fork: when script is edited and cast status is beyond draft (audio already generated),
  // snapshot current version before modifications
  const forkTriggered = useRef(false);
  const triggerForkIfNeeded = useCallback(async () => {
    if (forkTriggered.current) return;
    const postDraftStatuses = ["generating_tts", "tts_ready", "generating_videos", "generating", "ready", "scheduled", "live", "completed"];
    if (!postDraftStatuses.includes(cast.status?.toLowerCase())) return;
    forkTriggered.current = true;
    try {
      const result = await castsApi.fork(cast.id);
      toast({
        title: `Version saved (v${result.old_version})`,
        description: "Your previous version was preserved. Editing continues on the new version.",
      });
    } catch {
      // Fork is best-effort — don't block editing
    }
  }, [cast.id, cast.status]);

  // 6.6.3 — Per-block textarea refs for gesture autocomplete
  const textareaRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});

  // 4.7.8 — Captions toggles
  const [captionsGlobal, setCaptionsGlobal] = useState(true);
  const [captionsPerBlock, setCaptionsPerBlock] = useState<Record<string, boolean>>({});

  // 4.7.1 — transient "what this category change does" note, keyed by block id.
  // Set on a manual category switch so the change isn't a silent surprise;
  // cleared when the user dismisses it or switches again.
  const [categoryNotice, setCategoryNotice] = useState<Record<string, string>>({});

  // 4.7.4 — Per-block chat
  const [blockChatInputs, setBlockChatInputs] = useState<Record<string, string>>({});

  // 4.7.5 — Cast-level chat
  const [castChatInput, setCastChatInput] = useState("");
  const [castChatLoading, setCastChatLoading] = useState(false);

  // 4.7.4 — Undo/redo
  const history = useBlockHistory();

  // Fetch current cast
  const { data: freshCast, isLoading: loadingCast, refetch } = useQuery({
    queryKey: ["cast", cast.id],
    queryFn: () => castsApi.get(cast.id),
    refetchOnMount: true,
    // Poll while AI-from-product b-roll is still generating so the "creating
    // your product shot" tiles swap themselves out for the real shot with no
    // manual refresh. Stops as soon as every b-roll block is done/failed.
    refetchInterval: (q) => {
      const c = q.state.data as any;
      if (!c || c.broll_media_source !== "ai_generated") return false;
      const anyGenerating = (c.blocks || []).some(
        (b: any) => b?.metadata?.ai_broll === "generating",
      );
      return anyGenerating ? 5000 : false;
    },
  });

  // Smart-outline regeneration wipes existing blocks and creates new ones with
  // new IDs. If the user kicked off an edit (category change, look swap, motion
  // prompt, autosave, …) before our local state caught up, the PUT lands on a
  // stale block ID and the backend returns 404. This helper detects that case,
  // tells the user, and refetches the cast so the UI shows the fresh blocks.
  // Returns true when the error was the stale-id case (so callers can skip
  // their normal toast).
  const handleStaleBlockError = useCallback(async (err: any, fallbackTitle: string) => {
    const status = err?.response?.status;
    if (status === 404) {
      toast({
        title: "Blocks have been regenerated",
        description: "Refreshing — your edit was not saved. Please try again on the new block.",
        variant: "destructive",
      });
      await refetch();
      return true;
    }
    toast({
      title: fallbackTitle,
      description: err?.response?.data?.detail || err?.message,
      variant: "destructive",
    });
    return false;
  }, [refetch]);

  // Helper: always pick the active variant (sorted active-first by backend),
  // never fall back to position-0 alone — multi-variant blocks have one
  // is_active=true variant and we must edit / rewrite / display that one.
  // Inline `variants?.[0]` keeps slipping through code review which is why
  // edits sometimes hit the wrong variant.
  const pickActiveVariant = (b?: { variants?: any[] }) =>
    b?.variants?.find((v: any) => v.is_active) || b?.variants?.[0];

  useEffect(() => {
    if (!freshCast?.blocks) return;
    const sorted = [...freshCast.blocks].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
    if (sorted.length > 0) blocksEverLoadedRef.current = true;
    setBlocks((prev) => {
      const sameSet =
        prev.length > 0 &&
        prev.length === sorted.length &&
        prev.every((pb) => sorted.some((sb) => sb.id === pb.id));
      if (!sameSet) {
        // Fresh load or a regeneration (new block ids) — take the server set
        // as-is and seed edit history.
        sorted.forEach((b) => {
          const v = pickActiveVariant(b);
          if (v) history.push(b.id, { text: v.script_text || "", category: (b.category || "avatar_speaking") as string });
        });
        return sorted;
      }
      // Same blocks, background refetch (e.g. the AI-b-roll poll). Keep local
      // fields the user is editing (script text / category / product / look —
      // may have unsaved changes) and only pull the fields the server updates
      // out of band: b-roll media + AI-b-roll status + generated frame ids.
      const sById = new Map(sorted.map((sb) => [sb.id, sb]));
      return prev.map((pb) => {
        const sb = sById.get(pb.id);
        if (!sb) return pb;
        return {
          ...pb,
          parallel_media: sb.parallel_media,
          image_asset_id: sb.image_asset_id,
          video_asset_id: sb.video_asset_id,
          body_motion_start_look_id: sb.body_motion_start_look_id,
          body_motion_end_look_id: sb.body_motion_end_look_id,
          metadata: { ...(pb.metadata || {}), ai_broll: (sb.metadata as any)?.ai_broll },
        } as Block;
      });
    });
  }, [freshCast]);

  // Poll for blocks if none yet (auto-fired from Setup).
  //
  // Only when a script was genuinely NEVER produced — a fresh DRAFT cast that
  // landed here before Setup's generate chain finished. A cast already in
  // outline_review / script_review (or beyond) with zero blocks means the
  // user deleted every block on purpose; re-generating over that on a page
  // refresh (the bug this guard fixes) is wrong. blocksEverLoadedRef covers
  // the in-session delete; the status check covers a refresh.
  useEffect(() => {
    if (!freshCast) return;
    const hasBlocks = freshCast.blocks && freshCast.blocks.length > 0;
    const status = (freshCast.status as string | undefined)?.toLowerCase();
    const scriptNeverGenerated =
      !blocksEverLoadedRef.current && (status == null || status === "draft");
    if (!hasBlocks && !generating && scriptNeverGenerated) {
      setGenerating(true);
      const interval = setInterval(async () => {
        try {
          const updated = await castsApi.get(cast.id);
          if (updated.blocks && updated.blocks.length > 0) {
            clearInterval(interval);
            setGenerating(false);
            refetch();
          }
        } catch { /* ignore */ }
      }, 2000);
      // Setup's own auto-generate chain (outline, then a two-pass script
      // writer — two sequential LLM calls) routinely runs past 10s, which
      // used to fire this fallback while that chain was still in flight:
      // two concurrent outline/script generations for the same cast, one
      // of them building Variant rows against blocks the other had just
      // deleted, surfacing as a false "Script generation failed" toast
      // moments before the original call's real success. The backend now
      // serializes concurrent generation per cast (see the advisory lock in
      // casts.py's generate_outline/generate_scripts), so this can no
      // longer crash — but a shorter timeout still means paying for a
      // redundant LLM round trip more often than necessary.
      const fallback = setTimeout(() => {
        clearInterval(interval);
        generateOutline();
      }, 45000);
      return () => { clearInterval(interval); clearTimeout(fallback); };
    }
  }, [freshCast?.id, freshCast?.blocks?.length, freshCast?.status]);

  const generateOutline = useCallback(async () => {
    setGenerating(true);
    try {
      await castsApi.generateOutline(cast.id);
      await castsApi.generateScripts(cast.id);
      await refetch();
      toast({ title: "Script generated", description: "Review and edit the blocks below." });
    } catch (err: any) {
      toast({
        title: "Script generation failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setGenerating(false);
    }
  }, [cast.id, refetch]);

  const handleScriptChange = useCallback((blockId: string, variantId: string, text: string) => {
    // Trigger auto-fork on first edit if cast is beyond draft
    triggerForkIfNeeded();

    setBlocks(prev =>
      prev.map(b =>
        b.id === blockId
          ? { ...b, variants: b.variants?.map(v => v.id === variantId ? { ...v, script_text: text } : v) }
          : b
      )
    );

    const key = `${blockId}:${variantId}`;
    setPendingSaves(prev => new Set(prev).add(key));
    setSavedKeys(prev => { const n = new Set(prev); n.delete(key); return n; });

    if (saveTimers.current[key]) clearTimeout(saveTimers.current[key]);
    saveTimers.current[key] = setTimeout(async () => {
      try {
        await castsApi.updateVariant(cast.id, blockId, variantId, text);
        setPendingSaves(prev => { const n = new Set(prev); n.delete(key); return n; });
        setSavedKeys(prev => new Set(prev).add(key));
      } catch (err: any) {
        await handleStaleBlockError(err, "Failed to save");
        setPendingSaves(prev => { const n = new Set(prev); n.delete(key); return n; });
      }
    }, 600);
  }, [cast.id, triggerForkIfNeeded, handleStaleBlockError]);

  const handleScriptBlur = useCallback(async (blockId: string, variantId: string, text: string) => {
    const key = `${blockId}:${variantId}`;
    if (saveTimers.current[key]) { clearTimeout(saveTimers.current[key]); delete saveTimers.current[key]; }
    try {
      await castsApi.updateVariant(cast.id, blockId, variantId, text);
      setPendingSaves(prev => { const n = new Set(prev); n.delete(key); return n; });
      setSavedKeys(prev => new Set(prev).add(key));
    } catch (err: any) {
      await handleStaleBlockError(err, "Failed to save");
    }
  }, [cast.id, handleStaleBlockError]);

  const flushPendingSaves = useCallback(async () => {
    for (const key of Object.keys(saveTimers.current)) {
      clearTimeout(saveTimers.current[key]);
      delete saveTimers.current[key];
    }
    const saves: Promise<any>[] = [];
    for (const block of blocks) {
      for (const variant of block.variants || []) {
        if (variant?.script_text !== undefined) {
          saves.push(castsApi.updateVariant(cast.id, block.id, variant.id, variant.script_text));
        }
      }
    }
    await Promise.all(saves);
    setPendingSaves(new Set());
  }, [blocks, cast.id]);

  // 4.7.1 — Change block category
  const handleChangeCategory = useCallback(async (blockId: string, category: string) => {
    setBlocks(prev => prev.map(b => b.id === blockId ? { ...b, category: category } : b));
    // Surface what the switch does — a category change used to be a silent
    // no-op until a full regen; now the backend couples render_mode to it.
    setCategoryNotice(prev => ({
      ...prev,
      [blockId]: CATEGORY_MAP[category]?.onSwitch || "",
    }));
    try {
      await castsApi.updateBlock(cast.id, blockId, { category });
    } catch (err: any) {
      await handleStaleBlockError(err, "Failed to update category");
    }
  }, [cast.id, handleStaleBlockError]);

  // PR #83 — change a block's talking-head PIP layout. Persisted into
  // block_metadata.pip_layout via the existing PUT /blocks endpoint so
  // no new route is required. The renderer reads it back through
  // services.timeline_builder and routes pip_small / pip_medium / hidden
  // to the small-bake MuseTalk path; fullscreen keeps legacy behaviour.
  const handleChangePipLayout = useCallback(async (blockId: string, pipLayout: string) => {
    setBlocks(prev => prev.map(b => {
      if (b.id !== blockId) return b;
      const meta = { ...(b.metadata || {}), pip_layout: pipLayout };
      return { ...b, metadata: meta } as typeof b;
    }));
    try {
      await castsApi.updateBlock(cast.id, blockId, {
        metadata: { pip_layout: pipLayout },
      });
    } catch (err: any) {
      await handleStaleBlockError(err, "Failed to update avatar size");
    }
  }, [cast.id, handleStaleBlockError]);

  const handleRewriteInVoice = useCallback(async (blockId: string) => {
    try {
      const result = await scriptApi.rewriteInVoice(cast.id, blockId);
      toast({ title: "Rewritten in your voice", description: `Used ${result.corpus_entries_used} voice corpus entries.` });
      await refetch();
    } catch (err: any) {
      toast({ title: "Rewrite failed", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    }
  }, [cast.id, refetch]);

  // PR #66 Fix 4: scrolls the new block into view + focuses its script
  // textarea after add. Set by handleAddBlock so the user lands on the
  // freshly-created block instead of having to scroll. Cleared on next
  // user interaction.
  const [focusBlockId, setFocusBlockId] = useState<string | null>(null);

  // 4.7.7 — Add block with category. PR #66 Fix 4: returns the new
  // block id so the dropdown can scroll it into view; failures now
  // capture to Sentry so we stop swallowing silent errors.
  const handleAddBlock = useCallback(async (category = "avatar_speaking") => {
    try {
      const created = await castsApi.addBlock(cast.id, {
        block_type: "PRODUCT",
        sort_order: blocks.length,
        category,
      });
      await refetch();
      const newId =
        (created as { id?: string } | undefined)?.id ?? null;
      if (newId) {
        setFocusBlockId(newId);
      }
      toast({ title: "Block added" });
      return newId;
    } catch (err: any) {
      // Surface enough detail so the user knows WHY add failed instead
      // of a silent error toast. We don't have Sentry on the frontend
      // for arbitrary errors, but the 5xx interceptor in api.ts already
      // covers HTTP-level failures.
      toast({
        title: "Failed to add block",
        description:
          err?.response?.data?.detail || err?.message || "Unknown error",
        variant: "destructive",
      });
      return null;
    }
  }, [cast.id, blocks.length, refetch]);

  // PR #66 Fix 4: after a fresh block lands in `blocks`, scroll its
  // card into view + focus the script textarea so the right-panel
  // setup is immediately editable without manual scrolling. The
  // effect runs whenever focusBlockId changes AND the matching card
  // is in the DOM.
  useEffect(() => {
    if (!focusBlockId) return;
    const el = document.querySelector<HTMLElement>(
      `[data-block-id="${focusBlockId}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    const textarea = el.querySelector<HTMLTextAreaElement>(
      "textarea[data-script-textarea='true']",
    );
    if (textarea) {
      textarea.focus();
    }
    // Clear the focus token so a manual scroll won't keep retriggering.
    const t = window.setTimeout(() => setFocusBlockId(null), 800);
    return () => window.clearTimeout(t);
  }, [focusBlockId, blocks]);

  const handleDeleteBlock = useCallback(async (blockId: string) => {
    const isLastBlock = blocks.length <= 1;
    const ok = await confirmAction({
      title: isLastBlock ? "Delete the last block?" : "Delete block?",
      text: isLastBlock
        ? "This empties your script. You can add a new block or regenerate afterwards."
        : "This removes the script text and any generated audio.",
      confirmButtonText: "Delete",
      cancelButtonText: "Cancel",
      icon: "warning",
    });
    if (!ok) return;
    try {
      await castsApi.deleteBlock(cast.id, blockId);
      setBlocks(prev => prev.filter(b => b.id !== blockId));
      toast({ title: "Block deleted" });
    } catch (err: any) {
      toast({
        title: "Failed to delete",
        description: err?.response?.data?.detail || err?.message,
        variant: "destructive",
      });
    }
  }, [cast.id, blocks.length]);

  const handleChangeBackground = useCallback(async (blockId: string, lookId: string) => {
    setBlocks(prev => prev.map(b => b.id === blockId ? { ...b, avatar_look_id: lookId || undefined } : b));
    try {
      await castsApi.updateBlock(cast.id, blockId, { avatar_look_id: lookId || null });
    } catch (err: any) {
      await handleStaleBlockError(err, "Failed to update background");
    }
  }, [cast.id, handleStaleBlockError]);

  // Body-motion (avatar acting) handlers. The avatar acting block stores
  // a starting look + ending look (already-rendered body angle images)
  // plus a textual prompt the AI uses to interpolate motion.
  const handleChangeBodyMotionLook = useCallback(
    async (blockId: string, slot: "start" | "end", lookId: string | null) => {
      const field = slot === "start" ? "body_motion_start_look_id" : "body_motion_end_look_id";
      const prevValue = (blocks.find((b) => b.id === blockId) as any)?.[field] ?? null;
      setBlocks((prev) =>
        prev.map((b) =>
          b.id === blockId ? { ...b, [field]: lookId || undefined } : b,
        ),
      );
      try {
        await castsApi.updateBlock(cast.id, blockId, { [field]: lookId || null });
      } catch (err: any) {
        // Revert optimistic block update so the carousel reflects the
        // server's true selection on the next render.
        setBlocks((prev) =>
          prev.map((b) =>
            b.id === blockId ? { ...b, [field]: prevValue || undefined } : b,
          ),
        );
        await handleStaleBlockError(err, "Failed to update pose");
        // Re-throw so the carousel can clear its own pending-select
        // state and surface a toast at the click site.
        throw err;
      }
    },
    [cast.id, blocks, handleStaleBlockError],
  );

  // Debounced motion prompt save — the textarea fires onChange on every
  // keystroke; we save 800ms after the user stops typing.
  const motionPromptTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const handleChangeBodyMotionPrompt = useCallback(
    (blockId: string, text: string) => {
      setBlocks((prev) =>
        prev.map((b) =>
          b.id === blockId ? { ...b, body_motion_prompt: text } : b,
        ),
      );
      const existing = motionPromptTimers.current[blockId];
      if (existing) clearTimeout(existing);
      motionPromptTimers.current[blockId] = setTimeout(async () => {
        try {
          await castsApi.updateBlock(cast.id, blockId, { body_motion_prompt: text });
        } catch (err: any) {
          await handleStaleBlockError(err, "Failed to save motion description");
        }
      }, 800);
    },
    [cast.id, handleStaleBlockError],
  );

  // avatar_action blocks: the motion description IS the prompt that
  // drives the I2V video. Stored in body_motion_prompt on the block
  // and saved via the same `motion_prompt` PATCH key that the backend
  // routes to body_motion_prompt for this category.
  const motionTextTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const handleChangeMotionPrompt = useCallback(
    (blockId: string, text: string) => {
      setBlocks((prev) =>
        prev.map((b) =>
          b.id === blockId
            ? { ...b, motion_prompt: text, body_motion_prompt: text }
            : b,
        ),
      );
      const existing = motionTextTimers.current[blockId];
      if (existing) clearTimeout(existing);
      motionTextTimers.current[blockId] = setTimeout(async () => {
        try {
          await castsApi.updateBlock(cast.id, blockId, { motion_prompt: text });
        } catch (err: any) {
          await handleStaleBlockError(err, "Failed to save motion description");
        }
      }, 800);
    },
    [cast.id, handleStaleBlockError],
  );

  // Drag-drop. HTML5 DnD basics + polish:
  //  - onDragStart sets a custom drag image (the whole block card cloned, not
  //    the default arrow + tiny snapshot from the drag handle).
  //  - onDragOver computes whether the cursor is in the TOP or BOTTOM half
  //    of the hovered card, so we can show a precise insertion indicator and
  //    figure out the target index without ambiguity.
  //  - We DON'T mutate the array during dragOver. Neighbors translate via CSS
  //    so the layout reads as smooth animation, and the actual order changes
  //    only on drop. This avoids the flicker that comes with mid-drag splices.
  //  - onDrop persists the new order. onDragEnd cleans up if drop was missed.
  const handleDragStart = (e: React.DragEvent, idx: number) => {
    setDragSrcIdx(idx);
    setDropTargetIdx(idx);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(idx));
    // Use the entire block card as the drag image instead of the tiny grip.
    // We walk up to the closest [data-block-card] ancestor so users see what
    // they're moving while it floats with the cursor.
    const card = (e.target as HTMLElement).closest<HTMLElement>("[data-block-card]");
    if (card) {
      const rect = card.getBoundingClientRect();
      // Slight horizontal offset so the cursor doesn't sit dead-center over
      // text — keeps the original grip-relative grab feel.
      e.dataTransfer.setDragImage(card, 24, Math.min(rect.height / 2, 60));
    }
  };
  const handleDragOver = (e: React.DragEvent, idx: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragSrcIdx === null) return;
    // Decide drop target by hover-half. If cursor is in the top half of card N,
    // we'd insert BEFORE N (target=N). If bottom half, AFTER N (target=N+1).
    const card = e.currentTarget as HTMLElement;
    const rect = card.getBoundingClientRect();
    const before = (e.clientY - rect.top) < rect.height / 2;
    let target = before ? idx : idx + 1;
    // If we'd land at the original spot or one past it (no-op), prefer src.
    if (target === dragSrcIdx || target === dragSrcIdx + 1) target = dragSrcIdx;
    if (target !== dropTargetIdx) setDropTargetIdx(target);
  };
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    if (dragSrcIdx === null || dropTargetIdx === null) {
      setDragSrcIdx(null);
      setDropTargetIdx(null);
      return;
    }
    if (dropTargetIdx === dragSrcIdx) {
      // No-op drop, just clear the visual state.
      setDragSrcIdx(null);
      setDropTargetIdx(null);
      return;
    }
    // Compute the new order: remove the dragged block, then insert at target.
    // If target > src, the insert index shifts down by 1 because we just
    // removed an item before it.
    const reordered = (() => {
      const next = [...blocks];
      const [moved] = next.splice(dragSrcIdx, 1);
      const insertAt = dropTargetIdx > dragSrcIdx ? dropTargetIdx - 1 : dropTargetIdx;
      next.splice(insertAt, 0, moved);
      return next;
    })();
    setBlocks(reordered);
    setDragSrcIdx(null);
    setDropTargetIdx(null);
    try {
      await castsApi.reorderBlocks(cast.id, reordered.map(b => b.id));
    } catch (err: any) {
      toast({
        title: "Reorder failed",
        description: err?.response?.data?.detail || "Refresh to see saved order.",
        variant: "destructive",
      });
    }
  };
  const handleDragEnd = () => {
    // Cleanup if drop landed outside any valid target (missed the list).
    setDragSrcIdx(null);
    setDropTargetIdx(null);
  };

  // Track per-block frame upload state so we can show a spinner + disable
  // the picker while bytes are flying. Keyed by `${blockId}:${slot}`.
  const [frameUploading, setFrameUploading] = useState<Record<string, boolean>>({});
  const handleFrameUpload = useCallback(async (
    blockId: string,
    slot: "first" | "last",
    file: File,
  ) => {
    const key = `${blockId}:${slot}`;
    setFrameUploading(prev => ({ ...prev, [key]: true }));
    try {
      const result = await castsApi.uploadBlockFrame(cast.id, blockId, slot, file);
      // Optimistic local update so the preview appears immediately.
      setBlocks(prev => prev.map(b =>
        b.id === blockId
          ? { ...b,
              [slot === "first" ? "gen_video_first_frame_key" : "gen_video_last_frame_key"]: result.r2_key }
          : b
      ));
      toast({ title: `${slot === "first" ? "First" : "Last"} frame uploaded` });
    } catch (err: any) {
      toast({
        title: "Frame upload failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setFrameUploading(prev => ({ ...prev, [key]: false }));
    }
  }, [cast.id]);
  const handleFrameClear = useCallback(async (
    blockId: string,
    slot: "first" | "last",
  ) => {
    try {
      const field = slot === "first" ? "gen_video_first_frame_key" : "gen_video_last_frame_key";
      await castsApi.updateBlock(cast.id, blockId, { [field]: "" });
      setBlocks(prev => prev.map(b =>
        b.id === blockId ? { ...b, [field]: null } : b
      ));
    } catch (err: any) {
      await handleStaleBlockError(err, "Could not clear frame");
    }
  }, [cast.id, handleStaleBlockError]);

  // 4.7.4 — Per-block AI chat
  const handleBlockChat = useCallback(async (blockId: string) => {
    const instruction = blockChatInputs[blockId]?.trim();
    if (!instruction) return;

    const block = blocks.find(b => b.id === blockId);
    const variant = pickActiveVariant(block);
    if (!block || !variant) {
      // Surface this rather than silently dropping the click.
      toast({
        title: "Can't refine yet",
        description: "This block has no script text. Click Regenerate first.",
        variant: "destructive",
      });
      return;
    }

    setBlockChatInputs(prev => ({ ...prev, [blockId]: "" }));

    try {
      // Push to undo history before rewrite
      history.push(blockId, { text: variant.script_text || "", category: (block.category || "avatar_speaking") as string });
      // Call Claude rewrite endpoint
      await scriptApi.rewrite(cast.id, blockId, variant.id, instruction);
      await refetch();
      toast({ title: "Block refined" });
    } catch (err: any) {
      toast({ title: "Refine failed", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    }
  }, [blockChatInputs, blocks, cast.id, history, refetch]);

  // 4.7.4 — Undo/Redo handlers
  const handleUndo = useCallback((blockId: string) => {
    const entry = history.undo(blockId);
    if (!entry) return;
    const block = blocks.find(b => b.id === blockId);
    const variant = pickActiveVariant(block);
    if (variant) {
      handleScriptChange(blockId, variant.id, entry.text);
    }
  }, [blocks, history, handleScriptChange]);

  const handleRedo = useCallback((blockId: string) => {
    const entry = history.redo(blockId);
    if (!entry) return;
    const block = blocks.find(b => b.id === blockId);
    const variant = pickActiveVariant(block);
    if (variant) {
      handleScriptChange(blockId, variant.id, entry.text);
    }
  }, [blocks, history, handleScriptChange]);

  // 4.7.5 — Cast-level chat (refine all blocks, not regenerate)
  const handleCastChat = useCallback(async () => {
    if (!castChatInput.trim()) return;
    setCastChatLoading(true);
    try {
      await scriptApi.refineAllBlocks(cast.id, castChatInput);
      await refetch();
      setCastChatInput("");
      toast({ title: "All blocks refined" });
    } catch (err: any) {
      toast({ title: "Cast-level refine failed", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    } finally {
      setCastChatLoading(false);
    }
  }, [castChatInput, cast.id, refetch]);

  const generateAudioMutation = useMutation({
    mutationFn: async (force: boolean = false) => {
      await flushPendingSaves();
      await castsApi.generateTts(cast.id, force);
      return castsApi.get(cast.id);
    },
    onSuccess: (updatedCast) => {
      toast({ title: "Audio generation started!" });
      onDone(updatedCast);
    },
    onError: (err: any) => {
      toast({
        title: "Failed to start audio generation",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    },
  });

  if (loadingCast) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="w-6 h-6 animate-spin text-accent" />
      </div>
    );
  }

  // 4.7.9 — Total word count: TTS-bound only. Uses the active variant
  // because that is what gets rendered into TTS / final video.
  const totalWords = blocks.reduce((sum, b) => {
    const variant = pickActiveVariant(b);
    return sum + (variant?.script_text ? ttsWordCount(variant.script_text) : 0);
  }, 0);
  const totalDuration = Math.round((totalWords / 2.5) * 10) / 10;

  /** Render script text with color-coded gesture [green] and prosody {cyan} markers */
  const formatScriptHtml = (text: string): string => {
    return text
      .replace(/\[gesture:(\w+)\]/g, '<span style="color: #22c55e; font-weight: 600">[gesture:$1]</span>')
      .replace(/\[(\w+)\]/g, '<span style="color: #22c55e; font-weight: 600">[$1]</span>')
      .replace(/\{(\w+)\}/g, '<span style="color: #06b6d4; font-weight: 600">{$1}</span>');
  };

  const jumpToBlock = useCallback((blockId: string) => {
    document
      .querySelector(`[data-block-id="${blockId}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  return (
    <>
      {renderInProgress && <RenderLockBanner onCancelRender={onCancelRender} />}
      <div
        className={cn(
          "flex justify-center gap-5 p-6 relative transition-opacity",
          renderInProgress && "opacity-60",
        )}
        inert={renderInProgress}
      >
      {!generating && blocks.length > 1 && (
        <BlockOrderRail
          blocks={blocks}
          dragSrcIdx={dragSrcIdx}
          dropTargetIdx={dropTargetIdx}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          onDragEnd={handleDragEnd}
          onJumpTo={jumpToBlock}
        />
      )}
      <div className="w-full min-w-0 max-w-3xl space-y-6 relative">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white flex items-center gap-2">
            <FileText className="w-5 h-5 text-accent" />
            Script Editor
          </h2>
          <p className="text-sm text-white/50 mt-1">
            {cast.description
              ? `Goal: "${cast.description.slice(0, 80)}${(cast.description?.length || 0) > 80 ? "..." : ""}"`
              : "Edit the script blocks below"}
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right shrink-0">
            <div className="text-sm text-white/70 whitespace-nowrap">
              {totalWords} words · ~{totalDuration}s
            </div>
            <div className="text-xs text-white/40 whitespace-nowrap">{blocks.length} blocks</div>
          </div>
        </div>
      </div>

      {/* PR #65: clip-on lavalier vs phone-mic toggle. Persisted per-avatar
          (NOT per-cast — the toast makes that explicit, since this control
          sitting inside one cast's Script tab previously read like a
          cast-specific override). It's also only the fallback now: any
          block whose scene has its own mic-visible setting uses that
          instead — see AvatarLookPicker / mic_presets.resolve_scene_voice_settings. */}
      {cast.avatar_id && (
        <ClipMicToggle
          avatarId={cast.avatar_id}
          initialEnabled={!!castAvatar?.clip_mic_enabled}
          onChange={(enabled) => {
            // Keep the cached avatar in sync so the toggle survives a
            // refresh / remount (it reads back from this query).
            queryClient.invalidateQueries({ queryKey: ["avatar-status", cast.avatar_id] });
            toast({
              title: `Default mic style: ${enabled ? "clip mic" : "phone mic"}`,
              description: "Applies to every cast using this avatar (unless a scene sets its own). Regenerate TTS to apply to existing blocks here.",
            });
          }}
        />
      )}

      {/* Match a past live for this cast only (optional, non-blocking).
          Cast-scoped reference takes precedence over the avatar's at
          outline generation (resolve_active_assessment). */}
      <div className="rounded-lg border border-white/10 bg-white/5 p-4">
        <LiveReferenceCard
          castId={cast.id}
          title="Match a past live (optional)"
          subtitle="Reference a specific past live for this cast only."
        />
      </div>

      {/* 4.7.5 — Cast-level chat */}
      {!generating && blocks.length > 0 && (
        <div className="flex items-center gap-2 bg-white/5 rounded-lg px-3 py-2 border border-white/10">
          <MessageSquare className="w-4 h-4 text-white/30 shrink-0" />
          <input
            value={castChatInput}
            onChange={e => setCastChatInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !e.shiftKey && handleCastChat()}
            placeholder="Refine all blocks... (e.g. 'make all blocks shorter and more urgent')"
            className="flex-1 bg-transparent text-sm text-white/80 placeholder:text-white/30 focus:outline-none"
            disabled={castChatLoading}
          />
          <button
            onClick={handleCastChat}
            disabled={castChatLoading || !castChatInput.trim()}
            className="text-accent hover:text-accent/80 disabled:text-white/20"
          >
            {castChatLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
      )}

      {/* Generating skeleton */}
      {generating && (
        <div className="space-y-3">
          <div className="flex items-center gap-3 text-accent mb-4">
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="text-sm">Generating script...</span>
          </div>
          {[1, 2, 3].map(i => (
            <div key={i} className="border border-white/10 rounded-lg p-4 space-y-3 animate-pulse">
              <div className="flex items-center gap-2">
                <div className="w-6 h-4 bg-white/10 rounded" />
                <div className="w-16 h-4 bg-white/10 rounded" />
                <div className="w-20 h-4 bg-white/10 rounded" />
              </div>
              <div className="space-y-2">
                <div className="h-3 bg-white/5 rounded w-full" />
                <div className="h-3 bg-white/5 rounded w-4/5" />
                <div className="h-3 bg-white/5 rounded w-3/5" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty state — two cases:
          (a) the user deleted every block themselves → offer Add Block +
              Regenerate, and don't call it a "failure";
          (b) generation genuinely failed / hasn't arrived → Retry.
          We know it's (a) if we've ever rendered a block this session, or the
          cast is already past outline generation. */}
      {!generating && blocks.length === 0 && (() => {
        const s = (cast.status as string | undefined)?.toLowerCase();
        const userEmptied =
          blocksEverLoadedRef.current ||
          (!!s && !["draft", "generating", "generation_failed", "template_select", "pending_payment"].includes(s));
        return (
          <div className="text-center py-12 space-y-4">
            {userEmptied ? (
              <>
                <p className="text-white/40 text-sm">This script has no blocks.</p>
                <div className="mx-auto flex max-w-xs flex-col items-stretch gap-2 sm:max-w-md sm:flex-row sm:justify-center">
                  <div className="w-full sm:w-56">
                    <AddBlockButton onAdd={handleAddBlock} />
                  </div>
                  <Button onClick={generateOutline} variant="outline" className="shrink-0">
                    <Wand2 className="w-4 h-4 mr-2" /> Regenerate script
                  </Button>
                </div>
              </>
            ) : (
              <>
                <p className="text-white/40 text-sm">Script generation failed or is still loading.</p>
                <Button onClick={generateOutline} className="bg-accent hover:bg-accent/90">
                  <Wand2 className="w-4 h-4 mr-2" /> Retry Script Generation
                </Button>
              </>
            )}
          </div>
        );
      })()}

      {/* Script blocks */}
      {!generating && blocks.length > 0 && (
        <div className="space-y-3">
          {blocks.map((block, idx) => {
            // Pick the active variant explicitly. The backend now returns variants
            // sorted active-first, but a block may have multiple variants where only
            // one is active — explicit `find` is safer than relying on ordering.
            // If neither is_active nor [0] yields a variant (race during generation,
            // partial fetch, or stale cache), we render a small "loading text…"
            // hint so the user knows scripts are still arriving rather than seeing
            // a silent empty card with 0 words.
            const variant = pickActiveVariant(block);
            const rawText = variant?.script_text || "";
            const wc = ttsWordCount(rawText);
            const dur = estimateDuration(rawText);
            const cat = CATEGORY_MAP[(block.category || "avatar_speaking") as string] || CATEGORY_MAP.avatar_speaking;
            const isAvatarCategory = [
              "avatar_speaking",
              "avatar_action",
              // Legacy aliases — old blocks render correctly until SQL migration runs.
              "avatar_acting",
              "avatar_motion",
              "avatar_voiceover",
              "pip_talking_head",
            ].includes(block.category || "avatar_speaking");
            const blockCaptionOn = captionsPerBlock[block.id] ?? captionsGlobal;
            // Speaking / talking-head blocks carry an extra "Avatar size"
            // dropdown in the header, which pushes the word-count + edit
            // controls onto a wrapped second line. For those, render that
            // cluster just above the script textarea instead.
            const hasAvatarSizeDropdown =
              block.category === "avatar_speaking" || block.category === "pip_talking_head";
            // The outline normaliser retyped this beat (a single b-roll clip
            // would otherwise have covered the whole avatar_speaking shot).
            // Only badge it while the block still IS the auto-assigned type —
            // once the user switches away the badge naturally clears.
            const autoCat = block.metadata?.auto_categorized;
            const showAutoCatBadge =
              !!autoCat && autoCat.to === (block.category || "avatar_speaking");
            const switchNotice = categoryNotice[block.id];

            // Word count + per-block edit controls. Rendered in the header
            // normally, but moved to just above the textarea when the header
            // also carries the Avatar-size dropdown (see hasAvatarSizeDropdown).
            const metaControls = (
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-xs text-white/40 whitespace-nowrap shrink-0">{wc} words · ~{dur}s</span>

                {/* 4.7.8 — Per-block caption toggle */}
                <button
                  onClick={() => setCaptionsPerBlock(prev => ({
                    ...prev,
                    [block.id]: !(prev[block.id] ?? captionsGlobal),
                  }))}
                  className={cn(
                    "p-1 rounded",
                    blockCaptionOn ? "text-accent/60" : "text-white/20"
                  )}
                  title={blockCaptionOn ? "Captions on for this block" : "Captions off for this block"}
                >
                  <Captions className="w-3 h-3" />
                </button>

                {/* 4.7.4 — Undo / Redo */}
                <button
                  onClick={() => handleUndo(block.id)}
                  disabled={!history.canUndo(block.id)}
                  className="text-white/20 hover:text-white/50 disabled:opacity-30"
                  title="Undo (Ctrl+Z)"
                >
                  <Undo2 className="w-3 h-3" />
                </button>
                <button
                  onClick={() => handleRedo(block.id)}
                  disabled={!history.canRedo(block.id)}
                  className="text-white/20 hover:text-white/50 disabled:opacity-30"
                  title="Redo (Ctrl+Shift+Z)"
                >
                  <Redo2 className="w-3 h-3" />
                </button>

                <button
                  onClick={() => handleRewriteInVoice(block.id)}
                  className="text-xs text-purple-400 hover:text-purple-300 flex items-center gap-1"
                  title="Rewrite in your voice"
                >
                  <RefreshCw className="w-3 h-3" /> Voice
                </button>
                <button
                  onClick={() => handleDeleteBlock(block.id)}
                  className="text-xs text-red-400 hover:text-red-300"
                  title="Delete block"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );

            // Animated slide-aside for neighbors during drag. The dragged
            // block stays in place (just dimmed) while neighbors translate to
            // open a gap at dropTargetIdx. We compute a transform per index:
            //   - dragging from src to a target ABOVE src: items in [target..src-1]
            //     should slide DOWN by one card-height
            //   - dragging from src to a target BELOW src: items in [src+1..target-1]
            //     should slide UP by one card-height
            // Card height varies; using transform keeps it pixel-precise via
            // a CSS variable measured per-card with the resize observer is
            // overkill, so we use a uniform 14px gap + relative-position trick.
            const isDragging = dragSrcIdx === idx;
            const showIndicatorAbove = dropTargetIdx === idx && dragSrcIdx !== null && dragSrcIdx !== idx;
            const showIndicatorBelow = dropTargetIdx === idx + 1 && dragSrcIdx !== null && dragSrcIdx !== idx;
            // Slide-aside translation: positive = down, negative = up.
            let slideY = 0;
            if (dragSrcIdx !== null && dropTargetIdx !== null && !isDragging) {
              const slideAmount = 8; // px nudge — enough to feel kinetic, not jarring
              if (dragSrcIdx < idx && idx < dropTargetIdx) slideY = -slideAmount; // shift up
              if (dropTargetIdx <= idx && idx < dragSrcIdx) slideY = slideAmount; // shift down
            }
            return (
              <div
                key={block.id}
                data-block-card
                data-block-id={block.id}
                onDragOver={e => handleDragOver(e, idx)}
                onDrop={handleDrop}
                onDragEnd={handleDragEnd}
                style={{
                  transform: `translateY(${slideY}px)`,
                  transition: "transform 200ms cubic-bezier(.2,.8,.2,1), opacity 150ms, box-shadow 150ms",
                  opacity: isDragging ? 0.45 : 1,
                  boxShadow: isDragging ? "0 12px 28px -10px rgba(0,0,0,0.55)" : undefined,
                }}
                className={cn(
                  "relative border rounded-2xl p-4",
                  isDragging
                    ? "border-accent/60 bg-accent/10 ring-1 ring-accent/30 z-10"
                    : "border-white/10 bg-white/[0.03] hover:border-white/15 hover:bg-white/[0.05] transition-colors",
                )}
              >
                {/* Drop indicator: 2px accent bar above or below the card. */}
                {showIndicatorAbove && (
                  <span
                    aria-hidden
                    className="pointer-events-none absolute left-2 right-2 -top-[7px] h-[3px] rounded-full bg-accent shadow-[0_0_12px_rgba(167,139,250,.6)]"
                  />
                )}
                {showIndicatorBelow && (
                  <span
                    aria-hidden
                    className="pointer-events-none absolute left-2 right-2 -bottom-[7px] h-[3px] rounded-full bg-accent shadow-[0_0_12px_rgba(167,139,250,.6)]"
                  />
                )}
                {/* Two-column body: 9:16 visual preview on the left,
                    script + controls on the right. The original card
                    chrome (drag handle, header, controls) lives inside
                    the right column so the preview gets to shine. */}
                <div className="flex gap-4">
                  <BlockVisualPreview
                    block={block}
                    avatarFaceUrl={avatarFaceUrl}
                    durationSeconds={dur}
                    textOverlay={block.category === "stock_photo" ? rawText : null}
                    actionStartUrl={
                      // PR #66 Fix 2: prefer the server-hydrated
                      // first_frame_url so block #2+ render their
                      // start frame on initial mount. Falls back to
                      // looking the look up in `allLooks` when the
                      // server response predates this PR.
                      (block as any).first_frame_url ??
                      allLooks.find((l) => l.id === block.body_motion_start_look_id)?.image_url ??
                      null
                    }
                    actionEndUrl={
                      (block as any).last_frame_url ??
                      allLooks.find((l) => l.id === block.body_motion_end_look_id)?.image_url ??
                      null
                    }
                  />
                  <div className="flex-1 min-w-0 space-y-3">
                {/* Block header */}
                <div className="flex items-center justify-between flex-wrap gap-y-1.5">
                  <div className="flex items-center gap-2 shrink-0">
                    {/* Reordering now lives in the Block Order rail (left
                        sidebar) — no per-card drag handle. The card is still a
                        drop target so a drag from the rail can land on it. */}
                    <span className="text-xs font-medium text-white/60">Block {idx + 1}</span>

                    {/* 4.7.1 — Category dropdown. `title` gives the "what is
                        this block type" blurb on hover; the consequence of a
                        switch is shown inline under the header (switchNotice). */}
                    <div className="relative inline-block">
                      <select
                        value={block.category || "avatar_speaking"}
                        onChange={e => handleChangeCategory(block.id, e.target.value)}
                        title={cat.blurb}
                        className={cn(
                          "appearance-none text-[11px] pl-2 pr-6 py-1 rounded-full cursor-pointer border-0 focus:outline-none focus:ring-1 focus:ring-accent/40 font-medium",
                          cat.pillClass
                        )}
                      >
                        {BLOCK_CATEGORIES.map(c => (
                          <option key={c.value} value={c.value} title={c.blurb}>{c.label}</option>
                        ))}
                      </select>
                      <ChevronDown className="absolute right-1.5 top-1/2 -translate-y-1/2 w-3 h-3 pointer-events-none opacity-60" />
                    </div>

                    {/* 4.7.6 — Preview card summary */}
                    <span className="text-[10px] text-white/30 ml-1">
                      {isAvatarCategory ? "1 avatar" : block.category?.includes("stock") ? "stock media" : "AI media"}
                    </span>

                    {/* PR #83 — Avatar size: only meaningful for blocks
                        where the avatar is the visible face on camera
                        (speaking / pip_talking_head). Hidden on
                        voiceover / action / stock blocks because the
                        rendered face geometry doesn't apply. Default
                        comes from block_metadata.pip_layout (LLM picks
                        pip_small for product/feature beats, fullscreen
                        for direct-camera greeting/closing/CTA). */}
                    {(block.category === "avatar_speaking" || block.category === "pip_talking_head") && (
                      <div className="relative inline-block ml-1">
                        <select
                          value={(block.metadata?.pip_layout as string) || "fullscreen"}
                          onChange={e => handleChangePipLayout(block.id, e.target.value)}
                          className="appearance-none text-[10px] pl-2 pr-5 py-0.5 rounded-full cursor-pointer border border-white/15 bg-white/5 text-white/70 hover:text-white focus:outline-none focus:ring-1 focus:ring-accent/40"
                          title="Avatar size on screen"
                        >
                          <option value="fullscreen">Fullscreen</option>
                          <option value="pip_small">Small (default)</option>
                          <option value="pip_medium">Medium</option>
                          <option value="hidden">Hidden (audio only)</option>
                        </select>
                        <ChevronDown className="absolute right-1 top-1/2 -translate-y-1/2 w-2.5 h-2.5 pointer-events-none opacity-60" />
                      </div>
                    )}
                  </div>

                  {!hasAvatarSizeDropdown && metaControls}
                </div>

                {/* Auto-retype notice — the outline normaliser changed this
                    beat's type because a single b-roll clip would have covered
                    the whole avatar shot. Tell the user why + one-click undo. */}
                {showAutoCatBadge && autoCat && (
                  <div className="flex items-start gap-2 rounded-md border border-blue-500/30 bg-blue-500/10 px-2.5 py-1.5 text-[11px] text-blue-100/90">
                    <Sparkles className="w-3 h-3 mt-0.5 shrink-0 text-blue-300" />
                    <span className="flex-1">
                      Auto-set to <strong>{CATEGORY_MAP[autoCat.to]?.label || autoCat.to}</strong> — this
                      beat is short and fully covered by b-roll, so no avatar is generated.
                    </span>
                    <button
                      onClick={() => handleChangeCategory(block.id, autoCat.from)}
                      className="shrink-0 font-medium text-blue-200 underline decoration-blue-400/50 hover:text-white"
                    >
                      Keep as {CATEGORY_MAP[autoCat.from]?.label || autoCat.from}
                    </button>
                  </div>
                )}

                {/* Consequence of a manual category switch (was previously a
                    silent no-op until a full regen). */}
                {switchNotice && (
                  <div className="flex items-start gap-2 rounded-md border border-white/10 bg-white/5 px-2.5 py-1.5 text-[11px] text-white/60">
                    <span className="flex-1">{switchNotice}</span>
                    <button
                      onClick={() => setCategoryNotice(prev => {
                        const next = { ...prev };
                        delete next[block.id];
                        return next;
                      })}
                      className="shrink-0 text-white/40 hover:text-white/80"
                      title="Dismiss"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                )}

                {/* 4.7.2 — Per-category inline controls.
                    Avatar-bearing blocks (speaking / voiceover / pip)
                    get a thumbnail-style background picker. The picker
                    also lets the user generate a brand-new background
                    inline without leaving the script editor. */}
                {isAvatarCategory && (
                  <div className="space-y-1.5">
                    <div className="text-[10px] font-medium uppercase tracking-wider text-white/45">
                      Scene
                    </div>
                    <AvatarLookPicker
                      avatarId={cast.avatar_id}
                      looks={backgroundLooks}
                      value={block.avatar_look_id || null}
                      onChange={(lookId) => handleChangeBackground(block.id, lookId || "")}
                      defaultLabel="Cast scene"
                      onLookCreated={() => refetchLooks()}
                      size="sm"
                    />
                  </div>
                )}

                {/* Avatar acting (body motion). Two pose pickers: where
                    the avatar starts and where they end. The avatar
                    look images come from already-rendered body_motion
                    looks on the avatar profile, so the user picks two
                    real frames the renderer can morph between. The
                    text field is the motion description the AI uses
                    to interpolate.

                    If no body-motion looks exist yet, surface a
                    helpful link to /avatar/{id} so the user can render
                    the angle set first. */}
                {/* avatar_action: AI-generated SCENE frames (FLUX Kontext)
                    interpolated by I2V. Replaces the legacy avatar_motion
                    (T2V, no face) and avatar_acting (generic body shots)
                    categories. The avatar's appearance is auto-prepended to
                    every prompt on the server. */}
                {(block.category === "avatar_action" ||
                  block.category === "avatar_motion" ||
                  block.category === "avatar_acting") && (
                  <div className="space-y-2 rounded-lg border border-orange-400/15 bg-orange-500/[0.04] p-2.5">
                    <ActionFrameCarousel
                      castId={cast.id}
                      blockId={block.id}
                      startLookId={(block as any).body_motion_start_look_id || null}
                      endLookId={(block as any).body_motion_end_look_id || null}
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
                      productName={
                        (block.product_id &&
                          cast.products?.find((p) => p.id === block.product_id)?.name) ||
                        cast.products?.[0]?.name ||
                        null
                      }
                      onSelectionChange={(kind, lookId) =>
                        handleChangeBodyMotionLook(block.id, kind, lookId)
                      }
                    />
                    <div className="space-y-1">
                      <div className="text-[10px] font-medium uppercase tracking-wider text-orange-200/80">
                        Motion description — the actual movement (avatar appearance is auto-prepended)
                      </div>
                      <textarea
                        value={
                          (block as any).motion_prompt ||
                          (block as any).body_motion_prompt ||
                          ""
                        }
                        onChange={(e) => handleChangeMotionPrompt(block.id, e.target.value)}
                        placeholder="What actually moves + scene + camera: 'winds up and hurls the product at the brick wall, it bounces off, she catches it — handheld, punchy'"
                        rows={3}
                        className="w-full bg-black/25 border border-white/10 rounded-md px-2 py-1.5 text-[11px] text-white/85 placeholder:text-white/30 focus:outline-none focus:border-orange-400/50 resize-none"
                      />
                      <p className="text-[10px] text-white/35">
                        This is what drives the video — put the throw / walk / gesture here, not in the frame boxes above.
                        Keep it to <span className="text-white/55">one clear action</span>: the AI can't do step-by-step
                        sequences, real physics, or outcomes ("no dent", "hits her on the head", "proves it's tough") —
                        those get dropped. For a freer take, generate only a Start Frame and leave End Frame empty.
                        Voiceover below is optional — leave the script empty for a silent action shot.
                      </p>
                    </div>
                    {/* PR #76: per-block voiceover toggle. Action blocks
                        are never lip-synced; the dialogue plays as a
                        voiceover audio track over the motion clip. The
                        user can disable it for pure silent visual beats.
                        null/undefined = LLM default (treated as enabled
                        when dialogue is present). */}
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        className="w-3.5 h-3.5 accent-orange-400"
                        checked={(block as any).voiceover_enabled !== false}
                        onChange={async (e) => {
                          const next = e.target.checked;
                          setBlocks((prev) =>
                            prev.map((b) =>
                              b.id === block.id
                                ? { ...b, voiceover_enabled: next }
                                : b,
                            ),
                          );
                          try {
                            await castsApi.updateBlock(cast.id, block.id, {
                              metadata: { voiceover_enabled: next },
                            });
                          } catch (err) {
                            await handleStaleBlockError(
                              err,
                              "Failed to update voiceover toggle",
                            );
                          }
                        }}
                      />
                      <span className="text-[11px] text-white/80">
                        Voice over this action
                      </span>
                      <span
                        className={`ml-auto text-[10px] px-1.5 py-0.5 rounded ${
                          (block as any).voiceover_enabled !== false
                            ? "bg-orange-500/15 text-orange-200"
                            : "bg-white/5 text-white/40"
                        }`}
                        title={
                          (block as any).voiceover_enabled !== false
                            ? "Dialogue plays as a voiceover over the motion clip"
                            : "Silent action — dialogue dropped at render"
                        }
                      >
                        {(block as any).voiceover_enabled !== false
                          ? "🎙 voiceover"
                          : "🔇 silent"}
                      </span>
                    </label>
                  </div>
                )}

                {(block.category === "stock_photo" || block.category === "stock_video") && (
                  <VisualSourcePicker
                    castId={cast.id}
                    block={block}
                    onUpdated={() => {
                      queryClient.invalidateQueries({ queryKey: ["cast", cast.id] });
                    }}
                  />
                )}

                {/* Parallel b-roll — stock photo/video that plays ON TOP of
                    voiceover / PIP / avatar-speaking blocks. The avatar's
                    audio keeps running underneath; the b-roll covers the
                    canvas while the voice plays. The picker hits the
                    existing /api/stock-media/{photos,videos} endpoints. */}
                {["avatar_voiceover", "pip_talking_head", "avatar_speaking"].includes(
                  block.category || "avatar_speaking",
                ) && (
                  <ParallelMediaPicker
                    castId={cast.id}
                    block={block}
                    scriptText={rawText}
                    onUpdated={() => {
                      queryClient.invalidateQueries({ queryKey: ["cast", cast.id] });
                    }}
                  />
                )}

                {/* Product carousel — fades through ALL of the attached
                    product's media during the block render. The component
                    self-hides when the product has fewer than two assets,
                    so the section is invisible until PR #20's full media
                    import has populated the gallery. */}
                <ProductCarouselToggle
                  castId={cast.id}
                  block={block}
                  onUpdated={() => {
                    queryClient.invalidateQueries({ queryKey: ["cast", cast.id] });
                  }}
                />


                {block.category === "generated_photo" && (
                  <div className="text-[10px] text-white/30 italic">
                    AI photo will be generated based on block text prompt
                  </div>
                )}

                {/* Generated video: show first + last frame pickers. The text
                    prompt below is the motion description; the two frame slots
                    are optional but strongly recommended for control. */}
                {block.category === "generated_video" && (
                  <div className="space-y-2">
                    <div className="text-[10px] text-white/40">
                      First and last frame guide the AI video. Drop or click to upload —
                      both are optional, but the more you provide, the more controlled the result.
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <FrameSlot
                        label="First frame"
                        slot="first"
                        blockId={block.id}
                        r2Key={(block as any).gen_video_first_frame_key}
                        uploading={!!frameUploading[`${block.id}:first`]}
                        onUpload={(file) => handleFrameUpload(block.id, "first", file)}
                        onClear={() => handleFrameClear(block.id, "first")}
                      />
                      <FrameSlot
                        label="Last frame"
                        slot="last"
                        blockId={block.id}
                        r2Key={(block as any).gen_video_last_frame_key}
                        uploading={!!frameUploading[`${block.id}:last`]}
                        onUpload={(file) => handleFrameUpload(block.id, "last", file)}
                        onClear={() => handleFrameClear(block.id, "last")}
                      />
                    </div>
                  </div>
                )}

                {/* Per-block product pin. The cast's pre-selected products
                    (cast.products) are the only valid options — server
                    rejects anything else. Pinning a product makes the
                    action-frame dispatcher generate scene frames that hold
                    THIS product, and weaves the product name + description
                    into Refine/Voice rewrites of the script for this block.
                    Hidden when the cast has zero attached products. */}
                {(cast.products?.length ?? 0) > 0 && (
                  <BlockProductPicker
                    castId={cast.id}
                    blockId={block.id}
                    products={cast.products ?? []}
                    selectedProductId={block.product_id ?? null}
                    onChange={(productId) => {
                      setBlocks(prev => prev.map(b => b.id === block.id ? { ...b, product_id: productId ?? undefined } : b));
                    }}
                  />
                )}

                {/* "Featuring: <product>" hint sits directly above the
                    script textarea so the user sees what the rewriter will
                    weave in before they hit Refine. */}
                {block.product_id && (() => {
                  const pinned = cast.products?.find(p => p.id === block.product_id);
                  if (!pinned) return null;
                  return (
                    <div className="text-[11px] text-white/55 italic">
                      Featuring: <span className="text-white/80 not-italic font-medium">{pinned.name}</span>
                    </div>
                  );
                })()}

                {/* No-variant fallback. If a block has no variants, the script
                    generation is either still in flight or failed. Surface this
                    state explicitly with a retry button so the user isn't left
                    looking at an empty card showing only "0 words". */}
                {!variant && (
                  <div className="flex items-center justify-between text-xs text-amber-300/70 bg-amber-500/5 border border-amber-500/20 rounded px-3 py-2">
                    <span>Script text not loaded yet for this block.</span>
                    <button
                      onClick={generateOutline}
                      className="text-amber-300 hover:text-amber-200 underline underline-offset-2"
                    >
                      Regenerate
                    </button>
                  </div>
                )}

                {/* 4.7.3 + 6.6 — Text editor with prosody highlighting,
                    gesture autocomplete and autosave indicators. The
                    textarea sits behind a ProsodyHighlighter overlay
                    that colours (excited)/[pause]/etc — selection caret
                    stays in the textarea, only the colour comes from
                    the overlay. Geometry must match exactly: same font
                    size (text-sm), same line-height (leading-[1.6]),
                    same padding (px-3 py-2). */}
                {variant && (
                  <div className="space-y-1">
                    {/* Speaking / talking-head blocks: word count + edit
                        controls live here (not the header) so the header's
                        Avatar-size dropdown doesn't force a wrapped row. */}
                    {hasAvatarSizeDropdown && (
                      <div className="flex justify-end">{metaControls}</div>
                    )}
                    <div className="relative">
                      <ProsodyHighlighter text={rawText} />
                      <textarea
                        ref={el => { textareaRefs.current[block.id] = el; }}
                        data-script-textarea="true"
                        value={rawText}
                        onChange={e => {
                          handleScriptChange(block.id, variant.id, e.target.value);
                        }}
                        onBlur={() => handleScriptBlur(block.id, variant.id, rawText)}
                        onKeyDown={e => {
                          if ((e.metaKey || e.ctrlKey) && e.key === "z" && !e.shiftKey) {
                            e.preventDefault();
                            handleUndo(block.id);
                          }
                          if ((e.metaKey || e.ctrlKey) && e.key === "z" && e.shiftKey) {
                            e.preventDefault();
                            handleRedo(block.id);
                          }
                        }}
                        rows={3}
                        spellCheck={false}
                        // The textarea draws ONLY the caret + selection.
                        // Every visible glyph comes from the
                        // ProsodyHighlighter overlay above. We use
                        // text-transparent + caret-accent + a faint
                        // selection color so the user sees a normal
                        // caret but never two layers of text.
                        className="relative w-full bg-white/[0.03] border border-white/10 rounded-lg px-3 py-2 text-sm leading-[1.6] text-transparent caret-accent placeholder:text-white/30 resize-none focus:outline-none focus:border-accent/40 focus:bg-white/[0.05] selection:bg-accent/30 selection:text-transparent"
                        placeholder="Enter spoken script text…"
                      />
                      <div className="absolute bottom-1.5 right-2 flex items-center gap-2 text-[10px]">
                        <span className="text-white/25">{stripProsody(rawText).length}/280</span>
                        {pendingSaves.has(`${block.id}:${variant.id}`) ? (
                          <span className="text-yellow-400/60">Saving...</span>
                        ) : savedKeys.has(`${block.id}:${variant.id}`) ? (
                          <span className="text-green-400/60 flex items-center gap-0.5">
                            <Check className="w-2.5 h-2.5" /> Saved
                          </span>
                        ) : null}
                      </div>
                    </div>
                    {/* The gesture-validated highlight preview lives below
                        as <GestureHighlightedText/>. The earlier inline
                        formatScriptHtml duplicate has been removed so the
                        same script no longer renders three times (textarea
                        + green span preview + grey badge preview). */}
                    {/* 6.6.3 — Gesture autocomplete dropdown */}
                    <GestureAutocomplete
                        textareaRef={{ current: textareaRefs.current[block.id] ?? null }}
                        text={rawText}
                        onInsert={(replacement, start, end) => {
                          const before = rawText.slice(0, start);
                          const after = rawText.slice(end);
                          const newText = before + replacement + after;
                          handleScriptChange(block.id, variant.id, newText);
                          // Restore cursor position after React re-render
                          requestAnimationFrame(() => {
                            const el = textareaRefs.current[block.id];
                            if (el) {
                              const cursorPos = start + replacement.length;
                              el.focus();
                              el.setSelectionRange(cursorPos, cursorPos);
                            }
                          });
                        }}
                    />
                    {/* The legacy GestureHighlightedText preview was
                        rendering the script a second time below the
                        textarea, leading to a confusing "text appears
                        twice" effect. The new ProsodyHighlighter overlay
                        already covers prosody AND gesture markers in
                        place, so we don't need a separate preview row
                        anymore. Removed. */}
                  </div>
                )}

                {/* AI Refine bar — natural-language rewrite of the active
                    variant. Replaces the older block chat row. */}
                {variant && (
                  <RefineInput
                    castId={cast.id}
                    blockId={block.id}
                    variantId={variant.id}
                    currentText={rawText}
                    onRewritten={(newText) => {
                      handleScriptChange(block.id, variant.id, newText);
                      handleScriptBlur(block.id, variant.id, newText);
                    }}
                    compact
                  />
                )}

                {/* Per-block caption preset preview chip (Caption Fix 2,
                    spec lines 634-657). Only shown for blocks that
                    actually get captions burned in. Clicking "Change"
                    cycles through the 15 presets locally; the canonical
                    write happens in the arrange phase via the caption
                    style bar / inspector. */}
                {blockCaptionOn && block.category !== "stock_video" && (() => {
                  const blockPresetId =
                    ((cast as any)?.caption_preset?.id as string | undefined) ||
                    DEFAULT_CAPTION_PRESET_ID;
                  const blockPreset = getCaptionPreset(blockPresetId);
                  const cycleNext = () => {
                    const ids = Object.keys(CAPTION_PRESETS);
                    const i = ids.indexOf(blockPresetId);
                    const nextId = ids[(i + 1) % ids.length];
                    (cast as any).caption_preset = { id: nextId };
                    // Force a re-render via the existing per-block toggle map.
                    setCaptionsPerBlock((prev) => ({ ...prev }));
                  };
                  return (
                    <div className="mt-2 flex items-center gap-2 text-[10px] text-white/40">
                      <Type className="w-3 h-3" />
                      <span
                        style={{
                          fontFamily: blockPreset.fontFamily,
                          fontWeight: blockPreset.fontWeight,
                          color:
                            blockPreset.color === "transparent"
                              ? "#FFFFFF"
                              : blockPreset.color,
                          WebkitTextStroke:
                            blockPreset.strokeWidth &&
                            blockPreset.strokeColor !== "transparent"
                              ? `1px ${blockPreset.strokeColor}`
                              : undefined,
                          paintOrder: "stroke",
                          fontSize: 11,
                        }}
                      >
                        {blockPreset.name}
                      </span>
                      <button
                        type="button"
                        onClick={cycleNext}
                        className="ml-auto text-accent hover:text-accent/80"
                        title="Cycle caption preset for this block"
                      >
                        Change
                      </button>
                    </div>
                  );
                })()}
                  </div>
                </div>
                {/* Subtle per-block timeline ruler. Sits flush below the
                    two-column body so the duration is felt spatially,
                    not just read as a number in the header. */}
                <BlockTimeline durationSeconds={dur || 0} className="mt-4" />
              </div>
            );
          })}

          {/* 4.7.7 — Add Block with category picker */}
          <AddBlockButton onAdd={handleAddBlock} />

          {/* CHANGE 5.3 — Suggested Clips. Sits BELOW all block cards.
              `suggested_clips` is filled by cast_generator.suggest_clips
              after generate_scripts; `approved_clips` is the subset the
              user kept. Approving creates a child cast that renders via
              FFmpeg trim of the parent's mp4 (no GPU work). */}
          <SuggestedClipsSection cast={cast} blocks={blocks} />
        </div>
      )}

      {/* Bottom action bar */}
      {!generating && blocks.length > 0 && (
        <div className="flex items-center justify-between pt-4 border-t border-white/10">
          <Button variant="outline" onClick={generateOutline} className="border-white/20 text-white/70">
            <RefreshCw className="w-4 h-4 mr-2" /> Regenerate Script
          </Button>
          <Button
            size="lg"
            disabled={generateAudioMutation.isPending || blocks.length === 0}
            onClick={async () => {
              const hasExistingAudio = blocks.some(b =>
                (b.variants || []).some(v => !!(v as any).audio_key)
              );
              if (hasExistingAudio) {
                // Blocks already have audio — a plain run would skip them, so a
                // mic-style / scene change wouldn't take. Offer a full rebuild.
                const ok = await confirmAction({
                  title: "Regenerate all audio?",
                  text: "Every block already has audio. Regenerating replaces it for all of them — needed to apply a changed mic style or scene.",
                  confirmButtonText: "Regenerate all",
                  cancelButtonText: "Cancel",
                  icon: "warning",
                });
                if (!ok) return;
                generateAudioMutation.mutate(true);
                return;
              }
              generateAudioMutation.mutate(false);
            }}
            className="bg-accent hover:bg-accent/90"
          >
            {generateAudioMutation.isPending ? (
              <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Starting...</>
            ) : (
              <><Volume2 className="w-4 h-4 mr-2" /> Generate Audio &rarr;</>
            )}
          </Button>
        </div>
      )}
      </div>
      </div>
    </>
  );
}

/**
 * BlockOrderRail — compact left sidebar (desktop) for reordering script
 * blocks without dragging the tall block cards. Shares the exact
 * drag-src/drop-target state and handlers with the main list, so a drag
 * started here (or dropped here) reorders identically and persists via the
 * same castsApi.reorderBlocks call. Clicking a row scrolls to that block.
 */
function BlockOrderRail({
  blocks,
  dragSrcIdx,
  dropTargetIdx,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  onJumpTo,
}: {
  blocks: Block[];
  dragSrcIdx: number | null;
  dropTargetIdx: number | null;
  onDragStart: (e: React.DragEvent, idx: number) => void;
  onDragOver: (e: React.DragEvent, idx: number) => void;
  onDrop: (e: React.DragEvent) => void;
  onDragEnd: () => void;
  onJumpTo: (blockId: string) => void;
}) {
  return (
    <aside className="hidden lg:block w-44 shrink-0">
      <div className="sticky top-4 rounded-xl border border-white/10 bg-white/[0.03] p-2">
        <div className="px-1.5 pb-1.5 text-[10px] font-medium uppercase tracking-wider text-white/40">
          Block order
        </div>
        <div className="flex flex-col gap-0.5" onDrop={onDrop} onDragEnd={onDragEnd}>
          {blocks.map((block, idx) => {
            const cat =
              CATEGORY_MAP[(block.category || "avatar_speaking") as string] ||
              CATEGORY_MAP.avatar_speaking;
            const label = String(block.type || cat?.label || "Block").replace(/_/g, " ");
            const isDragging = dragSrcIdx === idx;
            const showAbove =
              dropTargetIdx === idx && dragSrcIdx !== null && dragSrcIdx !== idx;
            const showBelow =
              dropTargetIdx === idx + 1 && dragSrcIdx !== null && dragSrcIdx !== idx;
            return (
              <div
                key={block.id}
                className="relative"
                onDragOver={(e) => onDragOver(e, idx)}
              >
                {showAbove && (
                  <span
                    aria-hidden
                    className="pointer-events-none absolute inset-x-1 -top-[3px] h-[2px] rounded-full bg-accent"
                  />
                )}
                {showBelow && (
                  <span
                    aria-hidden
                    className="pointer-events-none absolute inset-x-1 -bottom-[3px] h-[2px] rounded-full bg-accent"
                  />
                )}
                <button
                  type="button"
                  draggable
                  onDragStart={(e) => onDragStart(e, idx)}
                  onClick={() => onJumpTo(block.id)}
                  title={`${label} — click to jump, drag to reorder`}
                  className={cn(
                    "flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left transition-colors cursor-grab active:cursor-grabbing",
                    isDragging
                      ? "bg-accent/15 ring-1 ring-accent/40"
                      : "hover:bg-white/[0.06]",
                  )}
                >
                  <GripVertical className="w-3 h-3 shrink-0 text-white/30" />
                  <span className="w-4 shrink-0 text-center text-[11px] font-semibold tabular-nums text-white/50">
                    {idx + 1}
                  </span>
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      cat?.dotClass || "bg-white/30",
                    )}
                  />
                  <span className="min-w-0 flex-1 truncate text-[11px] capitalize text-white/70">
                    {label.toLowerCase()}
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </aside>
  );
}

// Pose picker for the avatar_acting block. Renders the avatar's
// body_motion looks as horizontal thumbnails so the user picks the
// starting / ending pose from images they can actually see.
function BodyMotionLookSelect({
  looks,
  value,
  onChange,
}: {
  looks: AvatarLook[];
  value: string | null;
  onChange: (lookId: string | null) => void;
}) {
  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1 -mx-0.5 px-0.5">
      {looks.map((look) => {
        const thumb = look.image_url || (look.face_ref_key ? cdnUrl(look.face_ref_key) : null);
        const active = value === look.id;
        return (
          <button
            key={look.id}
            type="button"
            onClick={() => onChange(active ? null : look.id)}
            title={look.name}
            className={cn(
              "shrink-0 relative h-16 w-12 rounded-md overflow-hidden border-2 transition-all",
              active
                ? "border-violet-400 ring-2 ring-violet-400/30"
                : "border-white/10 hover:border-white/30",
            )}
          >
            {thumb ? (
              <img src={thumb} alt={look.name} className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full bg-gradient-to-br from-violet-500/15 to-violet-900/15" />
            )}
            <span className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent px-1 py-0.5">
              <span className="text-[8px] text-white/85 truncate block leading-tight">
                {look.pose_angle || look.name}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

// 4.7.7 — Add Block button with N-category picker.
// PR #66 Fix 4: rendered as the LAST tile in the block track so the
// dropdown sits flush with the existing rows visually. The user gets
// a dropdown of block categories; selecting one scaffolds the block
// and scrolls + focuses the new card's script textarea in the
// parent so the right-panel setup is immediately editable.
function AddBlockButton({
  onAdd,
}: {
  onAdd: (category: string) => Promise<string | null> | void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const handlePick = async (category: string) => {
    setOpen(false);
    setBusy(true);
    try {
      await onAdd(category);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      ref={ref}
      className="relative w-full"
      data-testid="add-block-track-tile"
    >
      <button
        onClick={() => setOpen(!open)}
        disabled={busy}
        className={cn(
          // Track-tile look — dashed border, fills row width, sits
          // visually flush with existing block cards.
          "w-full border-2 border-dashed border-white/10 rounded-lg p-3 flex items-center justify-center gap-2 text-white/40 hover:text-white/60 hover:border-white/20 transition-colors",
          busy && "opacity-60 cursor-wait",
        )}
        aria-label="Add block at end of track"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
        Add Block
        <ChevronDown className={cn("w-3 h-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 right-0 mt-1 bg-zinc-900 border border-white/10 rounded-lg p-2 grid grid-cols-2 gap-1 z-20 shadow-xl"
        >
          {BLOCK_CATEGORIES.map(c => (
            <button
              key={c.value}
              role="menuitem"
              title={c.blurb}
              onClick={() => handlePick(c.value)}
              className={cn(
                "text-[11px] px-3 py-2 rounded font-medium transition-colors hover:ring-1 hover:ring-accent/40 text-left flex items-center gap-2",
                c.pillClass
              )}
            >
              <span className={cn("w-2 h-2 rounded-full shrink-0", c.dotClass)} />
              {c.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── CHANGE 5.3 — Suggested Clips section ────────────────────────────────────
// Surfaces the LLM's clip proposals at the bottom of Script Editor. The user
// hits Keep to promote a clip into a child cast (which renders via FFmpeg
// trim on approve / on next render — see _render_clip_from_parent), or X to
// drop it from the suggestions list entirely.
type SuggestedClip = {
  name: string;
  block_ids: string[];
  duration_seconds?: number | null;
  best_platforms?: string[];
  why?: string;
};

function SuggestedClipsSection({ cast, blocks }: { cast: Cast; blocks: Block[] }) {
  const queryClient = useQueryClient();
  const suggested: SuggestedClip[] = ((cast as any).suggested_clips as SuggestedClip[] | undefined) || [];
  const approved: SuggestedClip[] = ((cast as any).approved_clips as SuggestedClip[] | undefined) || [];
  const approvedNames = new Set(approved.map((c) => c.name));

  // Visible suggestions = the parent's `suggested_clips`. We keep approved
  // ones in the list (showing "Approved ✓") so the user can see what they
  // kept; dismissed ones are removed by the backend when the user hits X.
  const [busyIdx, setBusyIdx] = useState<number | null>(null);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["cast", cast.id] });
    queryClient.invalidateQueries({ queryKey: ["ready-casts"] });
  };

  // Map block_id → 1-based position in current blocks for display. If a
  // block was deleted after suggestion, show "?" rather than a stale index.
  const positionByBlockId = new Map<string, number>();
  blocks.forEach((b, i) => positionByBlockId.set(b.id, i + 1));

  if (!suggested.length) return null;

  const handleApprove = async (i: number) => {
    setBusyIdx(i);
    try {
      await castsApi.approveClip(cast.id, i);
      toast({ title: "Clip approved", description: "Child cast created — render it from Publish.", variant: "success" });
      refresh();
    } catch (err: any) {
      toast({ title: "Could not approve clip", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    } finally {
      setBusyIdx(null);
    }
  };

  const handleDismiss = async (i: number) => {
    setBusyIdx(i);
    try {
      await castsApi.dismissClip(cast.id, i);
      refresh();
    } catch (err: any) {
      toast({ title: "Could not remove clip", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    } finally {
      setBusyIdx(null);
    }
  };

  return (
    <div className="mt-8 p-5 bg-white/[0.03] border border-white/10 rounded-xl">
      <div className="flex items-center gap-2 mb-4">
        <Scissors className="w-4 h-4 text-accent" />
        <h3 className="text-sm font-medium">Suggested Clips</h3>
        <span className="text-[10px] text-white/20 ml-auto">
          Standalone shorts generated from your full cast
        </span>
      </div>

      <div className="space-y-3">
        {suggested.map((clip, i) => {
          const isApproved = approvedNames.has(clip.name);
          const positions = (clip.block_ids || []).map((bid) => positionByBlockId.get(bid) ?? "?").join(", ");
          return (
            <div
              key={`${clip.name}-${i}`}
              className="flex items-center gap-4 p-3 bg-white/[0.02] border border-white/[0.05] rounded-lg"
            >
              <div className="w-8 h-14 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center flex-shrink-0">
                <Smartphone className="w-4 h-4 text-white/15" />
              </div>

              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{clip.name}</div>
                <div className="text-[11px] text-white/30 mt-0.5">
                  Blocks {positions}
                  {clip.duration_seconds ? ` · ${clip.duration_seconds}s` : ""}
                </div>
                {clip.why && <div className="text-[10px] text-white/20 mt-1">{clip.why}</div>}
                {clip.best_platforms && clip.best_platforms.length > 0 && (
                  <div className="flex gap-1.5 mt-1.5">
                    {clip.best_platforms.map((p) => (
                      <span key={p} className="text-[9px] px-1.5 py-0.5 bg-white/5 rounded-full text-white/30">
                        {p.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex gap-1.5 flex-shrink-0">
                {isApproved ? (
                  <span className="text-[10px] text-emerald-400 flex items-center gap-1 px-2">
                    <Check className="w-3 h-3" /> Approved
                  </span>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyIdx === i}
                    onClick={() => handleApprove(i)}
                  >
                    {busyIdx === i ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <Check className="w-3 h-3 mr-1" />}
                    Keep
                  </Button>
                )}
                {!isApproved && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busyIdx === i}
                    onClick={() => handleDismiss(i)}
                  >
                    <X className="w-3 h-3" />
                  </Button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-[10px] text-white/15 mt-3">
        Approved clips render alongside the full cast. Each gets its own caption in Publish.
      </p>
    </div>
  );
}
