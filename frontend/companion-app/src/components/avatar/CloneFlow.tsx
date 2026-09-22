/**
 * CloneFlow — 3-phase clone avatar creation flow (Phase 5 redesign).
 *
 * Phase 1: CloneSourcePhase    — merged upload/record/social + face select + identity fields
 * Phase 2: CloneAudiencePhase  — redesigned target audience module (dual sliders, gender lean, chips)
 * Phase 3: ClonePreviewPhase   — summary card + generate preview + approve
 */
import { useState, useRef, useCallback, useEffect } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  CheckCircle,
  Wand2,
  ArrowRight,
  Camera,
  Upload,
  Mic,
  X,
  ChevronLeft,
  ChevronRight,
  Play,
  Pause,
  Image as ImageIcon,
  FileAudio,
  Shuffle,
  Globe,
  Video,
  Search,
  StopCircle,
  RotateCcw,
  Sliders,
  AlertCircle,
} from "lucide-react";
import {
  Camera as CameraIcon,
  User,
  Film,
  Palette,
  MapPin,
  Sparkles,
  BookOpen,
  Sun,
  Heart,
  Zap,
  Aperture,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { avatarApi, voiceCorpusApi, userApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { StepIndicator } from "./StepIndicator";
import { PipelineProgressView } from "./PipelineProgressView";
import { VoiceCorpusTab } from "./VoiceCorpusTab";
import { ImageCropModal } from "./ImageCropModal";
import type { VoiceCorpusEntry } from "@/lib/types";
import { LAYOUT_OPTIONS, playerAspectRatio } from "@/lib/layoutOptions";

// ── Constants ──

const CLONE_STEPS = [
  { key: "source", label: "Source" },
  { key: "audience", label: "Audience" },
  { key: "preview", label: "Preview" },
];

type ClonePhase = "source" | "audience" | "preview";
type UploadMethod = "social" | "upload" | "record";

const DEFAULT_PREVIEW_TEXT =
  "Hello! Welcome to my channel. I'm excited to show you some amazing things today. Let's get started!";

const INTEREST_OPTIONS = [
  "Fitness", "Beauty", "Skincare", "Makeup", "Home & Kitchen", "Tech & Gadgets",
  "Fashion", "Outdoor & Camping", "Parenting", "Pets", "Cooking", "Health & Wellness",
  "Supplements", "Books", "Gaming", "Travel", "Crafts", "Sustainability",
  "Luxury", "Budget/Deals", "Moms", "Dads", "Gen Z", "Students",
  "Professionals", "Small Business", "Gardening", "Auto", "Sports", "Music",
  "Entertainment", "Finance", "Education", "Lifestyle",
];

const GENDER_SELECT_OPTIONS = [
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
  { value: "non-binary", label: "Non-binary" },
  { value: "prefer-not-to-say", label: "Prefer not to say" },
];

// Language dropdown removed — language is now auto-detected server-side via langdetect

const GEOGRAPHY_OPTIONS = [
  { value: "", label: "Any / Not specified" },
  { value: "north-america", label: "North America" },
  { value: "europe", label: "Europe" },
  { value: "latin-america", label: "Latin America" },
  { value: "asia-pacific", label: "Asia-Pacific" },
  { value: "middle-east", label: "Middle East" },
  { value: "africa", label: "Africa" },
  { value: "global", label: "Global" },
];

const INCOME_BRACKET_OPTIONS = [
  { value: "", label: "Any / Not specified" },
  { value: "low", label: "Low income" },
  { value: "lower-middle", label: "Lower-middle income" },
  { value: "middle", label: "Middle income" },
  { value: "upper-middle", label: "Upper-middle income" },
  { value: "high", label: "High income" },
];

const OCCUPATION_OPTIONS = [
  "Students", "Freelancers", "Entrepreneurs", "Corporate professionals",
  "Healthcare workers", "Teachers / Educators", "Creatives / Artists",
  "Tech workers", "Tradespeople", "Stay-at-home parents",
  "Retirees", "Military / Veterans", "Influencers / Content creators",
  "Small business owners", "Retail / Service workers",
];

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  camera: CameraIcon, user: User, film: Film, palette: Palette,
  "map-pin": MapPin, sparkles: Sparkles, "book-open": BookOpen,
  sun: Sun, heart: Heart, zap: Zap, aperture: Aperture,
  flower: Heart,
};

// ── Helpers ──

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── Types ──

interface FaceCandidate {
  url: string;
  r2_key: string;
  score: number;
}

interface UploadPhaseResult {
  candidates: FaceCandidate[];
  voiceReady: boolean;
  voiceFromFaceVideo: boolean;
  avatarId?: string;
}

interface SourcePhaseData {
  name: string;
  gender: string;
  voicePreviewText: string;
  selectedFaceIdx: number;
  candidates: FaceCandidate[];
  layout: string;
}

interface AudiencePhaseData {
  ageMin: number;
  ageMax: number;
  genderLean: number;
  genderDoesntMatter: boolean;
  interests: string[];
  geography: string;
  incomeBracket: string;
  occupations: string[];
  audienceDesc: string;
}

function findSelectedFaceIdx(candidates: FaceCandidate[], faceRefKey?: string | null): number {
  if (!candidates.length) return 0;
  if (!faceRefKey) return 0;
  const normalized = faceRefKey.replace("https://media.luminacast.com/", "");
  const idx = candidates.findIndex((candidate) =>
    candidate.r2_key === normalized
    || candidate.url === faceRefKey
    || candidate.url.endsWith(`/${normalized}`)
  );
  return idx >= 0 ? idx : 0;
}

function parseAudienceData(targetAudience?: Record<string, unknown> | null): AudiencePhaseData | null {
  if (!targetAudience) return null;

  const ageMin = typeof targetAudience.age_min === "number" ? targetAudience.age_min : 18;
  const ageMax = typeof targetAudience.age_max === "number" ? targetAudience.age_max : 45;
  const rawGenderLean = targetAudience.gender_lean;

  return {
    ageMin,
    ageMax,
    genderLean: typeof rawGenderLean === "number" ? rawGenderLean : 50,
    genderDoesntMatter: rawGenderLean === "any",
    interests: Array.isArray(targetAudience.interests)
      ? targetAudience.interests.filter((value): value is string => typeof value === "string")
      : [],
    geography: typeof targetAudience.geography === "string" ? targetAudience.geography : "",
    incomeBracket: typeof targetAudience.income_bracket === "string" ? targetAudience.income_bracket : "",
    occupations: Array.isArray(targetAudience.occupations)
      ? targetAudience.occupations.filter((value): value is string => typeof value === "string")
      : [],
    audienceDesc: typeof targetAudience.description === "string" ? targetAudience.description : "",
  };
}

// ── ShimmerField ──

function ShimmerField({ isLoading, children, className }: {
  isLoading: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  if (isLoading) {
    return (
      <div className={cn("relative overflow-hidden rounded-lg border border-border bg-surface", className)}>
        <div className="absolute inset-0 bg-linear-to-r from-transparent via-accent/10 to-transparent animate-shimmer" />
        <div className="p-3 space-y-2">
          <div className="h-4 bg-border/40 rounded w-3/4 animate-pulse" />
          <div className="h-4 bg-border/40 rounded w-1/2 animate-pulse" />
        </div>
      </div>
    );
  }
  return <>{children}</>;
}


// ═══════════════════════════════════════════════════════════════════
// UPLOAD SUB-PHASES (Social Media / Upload / Record)
// These are reused inside CloneSourcePhase's left column.
// ═══════════════════════════════════════════════════════════════════

// ── Social Media Sub-Phase ──

function CloneSocialMediaSubPhase({
  avatarId,
  ensureAvatarId,
  onComplete,
}: {
  avatarId?: string | null;
  ensureAvatarId: () => Promise<string>;
  onComplete: (result: UploadPhaseResult) => void;
}) {
  const [handle, setHandle] = useState("");
  const [scanId, setScanId] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [selectedVideoId, setSelectedVideoId] = useState<string | null>(null);
  const [selectedVideoKey, setSelectedVideoKey] = useState<string | null>(null);
  const [segmentStart, setSegmentStart] = useState(0);
  const [segmentEnd, setSegmentEnd] = useState(15);
  const [videoDuration, setVideoDuration] = useState(60);
  const [processing, setProcessing] = useState(false);

  // Poll scout status
  const { data: scoutData } = useQuery({
    queryKey: ["scout-status", scanId],
    queryFn: () => avatarApi.scoutStatus(scanId!),
    enabled: !!scanId,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      if (status === "completed" || status === "failed") return false;
      return 3000;
    },
  });

  const startScan = useCallback(async () => {
    const trimmed = handle.trim().replace(/^@/, "");
    if (!trimmed) {
      toast({ title: "Please enter a TikTok username", variant: "destructive" });
      return;
    }
    setScanning(true);
    try {
      const result = await avatarApi.scoutStart(trimmed);
      setScanId(result.scan_id);
    } catch (err: any) {
      toast({
        title: "Scout failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    } finally {
      setScanning(false);
    }
  }, [handle]);

  const selectVideo = useCallback((video: any) => {
    setSelectedVideoId(video.tiktok_video_id);
    const key = video.download_url
      ? video.download_url.replace("https://media.luminacast.com/", "")
      : null;
    setSelectedVideoKey(key);
    setVideoDuration(video.duration_seconds || 60);
    setSegmentStart(0);
    setSegmentEnd(Math.min(15, video.duration_seconds || 15));
  }, []);

  const handleProcessSegment = useCallback(async () => {
    if (!selectedVideoKey) {
      toast({ title: "No video selected", variant: "destructive" });
      return;
    }
    if (segmentEnd - segmentStart < 15) {
      toast({ title: "Segment must be at least 15 seconds", variant: "destructive" });
      return;
    }
    setProcessing(true);
    try {
      const ensuredAvatarId = avatarId || await ensureAvatarId();
      await avatarApi.processSegment(ensuredAvatarId, {
        video_r2_key: selectedVideoKey,
        start_seconds: segmentStart,
        end_seconds: segmentEnd,
      });
      // Poll for face candidates
      const pollCandidates = async (): Promise<FaceCandidate[]> => {
        for (let i = 0; i < 60; i++) {
          await new Promise((r) => setTimeout(r, 3000));
          try {
            const status = await avatarApi.status(ensuredAvatarId);
            if (status.status === "FACE_CANDIDATES_READY" || status.status === "CANDIDATES_READY") {
              const candidateData = await avatarApi.getFaceCandidates(ensuredAvatarId);
              return (candidateData.candidates || []).map((c: any) => ({
                url: c.url || c.face_url,
                r2_key: c.r2_key || "",
                score: c.score || 0,
              }));
            }
            if (status.status === "FAILED") throw new Error("Processing failed");
          } catch { /* keep polling */ }
        }
        throw new Error("Timeout waiting for face candidates");
      };
      const candidates = await pollCandidates();
      onComplete({ candidates, voiceReady: false, voiceFromFaceVideo: true, avatarId: ensuredAvatarId });
    } catch (err: any) {
      toast({
        title: "Segment processing failed",
        description: err?.message || "Please try again",
        variant: "destructive",
      });
    } finally {
      setProcessing(false);
    }
  }, [avatarId, ensureAvatarId, selectedVideoKey, segmentStart, segmentEnd, onComplete]);

  const scoutStatus = scoutData?.status;
  const videos = scoutData?.videos || [];

  return (
    <div className="space-y-5">
      {/* TikTok handle input */}
      <div className="space-y-3">
        <p className="text-xs text-text-muted">Enter a TikTok username or profile URL</p>
        <div className="flex gap-2">
          <input
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && startScan()}
            placeholder="@username or tiktok.com/@username"
            className="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted"
          />
          <Button
            onClick={startScan}
            disabled={scanning || !handle.trim()}
            size="sm"
            className="gap-1.5"
          >
            {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            Find Videos
          </Button>
        </div>
      </div>

      {/* Scanning progress */}
      {scanId && scoutStatus !== "completed" && scoutStatus !== "failed" && (
        <div className="flex flex-col items-center gap-3 py-6">
          <Loader2 className="h-8 w-8 text-accent animate-spin" />
          <p className="text-sm text-text-muted">
            Scanning TikTok videos... ({scoutData?.videos_analyzed || 0}/{scoutData?.videos_found || "?"} analyzed)
          </p>
        </div>
      )}

      {/* Scout failed */}
      {scoutStatus === "failed" && (
        <div className="text-center py-4">
          <p className="text-sm text-red-400">Scout failed: {scoutData?.error || "Unknown error"}</p>
        </div>
      )}

      {/* Video gallery */}
      {videos.length > 0 && !selectedVideoId && (
        <div className="space-y-3">
          <p className="text-sm font-medium text-text">Select a video ({videos.length} found)</p>
          <div className="grid grid-cols-4 gap-2">
            {videos.slice(0, 8).map((video: any) => (
              <button
                key={video.tiktok_video_id}
                onClick={() => selectVideo(video)}
                className="relative aspect-9/16 rounded-lg overflow-hidden border-2 border-border hover:border-accent/40 transition-all"
              >
                {video.thumbnail_url ? (
                  <img
                    src={video.thumbnail_url}
                    alt="Video thumbnail"
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="h-full w-full bg-surface flex items-center justify-center">
                    <Film className="h-5 w-5 text-text-muted" />
                  </div>
                )}
                <div className="absolute bottom-0 left-0 right-0 bg-linear-to-t from-black/60 to-transparent p-1">
                  <span className="text-[10px] text-white font-medium">
                    {video.duration_seconds ? `${Math.round(video.duration_seconds)}s` : ""}
                  </span>
                </div>
                {video.has_full_body && (
                  <div className="absolute top-1 right-1 h-4 w-4 rounded-full bg-green-500 flex items-center justify-center">
                    <CheckCircle className="h-3 w-3 text-white" />
                  </div>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Segment picker */}
      {selectedVideoId && (
        <div className="space-y-4 rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-text">Select segment</p>
            <button
              onClick={() => { setSelectedVideoId(null); setSelectedVideoKey(null); }}
              className="text-xs text-accent hover:underline"
            >
              Choose different video
            </button>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <label className="text-xs text-text-muted w-12">Start</label>
              <input
                type="range"
                min={0}
                max={Math.max(0, videoDuration - 15)}
                step={0.5}
                value={segmentStart}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  setSegmentStart(v);
                  if (segmentEnd - v < 15) setSegmentEnd(v + 15);
                }}
                className="flex-1"
              />
              <span className="text-xs text-text-muted w-12 text-right">{segmentStart.toFixed(1)}s</span>
            </div>
            <div className="flex items-center gap-3">
              <label className="text-xs text-text-muted w-12">End</label>
              <input
                type="range"
                min={segmentStart + 15}
                max={videoDuration}
                step={0.5}
                value={segmentEnd}
                onChange={(e) => setSegmentEnd(parseFloat(e.target.value))}
                className="flex-1"
              />
              <span className="text-xs text-text-muted w-12 text-right">{segmentEnd.toFixed(1)}s</span>
            </div>
            <p className="text-[10px] text-text-muted text-center">
              Segment duration: {(segmentEnd - segmentStart).toFixed(1)}s (minimum 15s)
            </p>
          </div>

          <Button
            onClick={handleProcessSegment}
            disabled={processing || !selectedVideoKey}
            className="w-full gap-2"
          >
            {processing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
            {processing ? "Processing segment..." : "Use This Segment"}
          </Button>
        </div>
      )}
    </div>
  );
}


// ── Upload Sub-Phase ──

function CloneUploadSubPhase({
  avatarId,
  ensureAvatarId,
  onComplete,
  onFaceReady,
  existingFace,
  layout,
}: {
  avatarId?: string | null;
  ensureAvatarId: () => Promise<string>;
  onComplete: (result: UploadPhaseResult) => void;
  onFaceReady?: (candidates: FaceCandidate[], ensuredAvatarId?: string) => void;
  existingFace?: FaceCandidate | null;   // NEW
  layout?: string;
}) {
  const qc = useQueryClient();
  // Face state
  const [faceCandidates, setFaceCandidates] = useState<FaceCandidate[]>(
    existingFace ? [existingFace] : []
  );
  const [faceStatus, setFaceStatus] = useState<"idle" | "uploading" | "processing" | "ready">(
    existingFace ? "ready" : "idle"
  );
  const [faceSourceType, setFaceSourceType] = useState<"image" | "video" | null>(null);
  const [faceFile, setFaceFile] = useState<{ name: string; size: number; thumbUrl?: string } | null>(null);
  const faceInputRef = useRef<HTMLInputElement>(null);
  // Photo awaiting manual crop confirmation — auto-framing isn't always
  // right, so images (not videos, which go through frame extraction
  // instead) get a manual crop step before upload.
  const [pendingCropFile, setPendingCropFile] = useState<File | null>(null);

  useEffect(() => {
    if (existingFace && faceCandidates.length === 0) {
      setFaceCandidates([existingFace]);
      setFaceStatus("ready");
    }
  }, [existingFace]);

  // Voice state
  const [voiceStatus, setVoiceStatus] = useState<"idle" | "uploading" | "processing" | "ready" | "failed">("idle");
  const [voiceFile, setVoiceFile] = useState<{ name: string; size: number } | null>(null);
  const [showVoiceOverride, setShowVoiceOverride] = useState(false);
  const voiceInputRef = useRef<HTMLInputElement>(null);
  const autoCompletedRef = useRef(false);

  // Voice extraction from face video
  const [voiceFromFaceVideo, setVoiceFromFaceVideo] = useState(false);

  // Poll voice corpus for readiness
  const { data: corpusData } = useQuery({
    queryKey: ["voice-corpus", avatarId],
    queryFn: () => voiceCorpusApi.list(avatarId!),
    enabled: !!avatarId,
    refetchInterval: (q) => {
      const entries: VoiceCorpusEntry[] = q.state.data?.entries || [];
      const hasProcessing = entries.some((e) => e.status === "pending" || e.status === "processing");
      return hasProcessing || voiceStatus === "processing" || voiceStatus === "uploading" ? 3000 : false;
    },
  });

  useEffect(() => {
    if (!corpusData) return;
    const entries: VoiceCorpusEntry[] = corpusData.entries || [];
    const hasReady = entries.some((e) => e.status === "ready");
    const hasProcessing = entries.some((e) => e.status === "pending" || e.status === "processing");
    const hasFailed = entries.some((e) => e.status === "failed");
    if (hasReady) {
      setVoiceStatus("ready");
    } else if (hasProcessing) {
      setVoiceStatus("processing");
    } else if (hasFailed) {
      setVoiceStatus("failed");
    }
  }, [corpusData]);

  // Face upload handler
  const handleFaceUpload = useCallback(async (file: File) => {
    setFaceStatus("uploading");
    setFaceCandidates([]);
    setVoiceFromFaceVideo(false);

    // Store file info + thumbnail for images
    const fileInfo: { name: string; size: number; thumbUrl?: string } = {
      name: file.name,
      size: file.size,
    };
    if (file.type.startsWith("image/")) {
      fileInfo.thumbUrl = URL.createObjectURL(file);
    }
    setFaceFile(fileInfo);

    try {
      setFaceStatus("processing");
      const isVideo = file.type.startsWith("video/");
      const ensuredAvatarId = avatarId || await ensureAvatarId();
      const result = await avatarApi.cloneUploadFace(ensuredAvatarId, file, isVideo, layout);
      setFaceCandidates(result.candidates);
      setFaceSourceType(result.source_type);
      setFaceStatus("ready");
      onFaceReady?.(result.candidates, ensuredAvatarId);

      // If face source is video and voice was extracted
      if (isVideo && result.voice_extraction === "processing") {
        setVoiceFromFaceVideo(true);
        setVoiceStatus("processing");
      }

      // For single-image upload: auto-complete immediately (no "Files Ready" click needed)
      // if (result.source_type === "image" && result.candidates.length === 1) {
      //   onComplete({ candidates: result.candidates, voiceReady: false, voiceFromFaceVideo: false });
      //   return;
      // }
    } catch (err: any) {
      setFaceStatus("idle");
      setFaceFile(null);
      toast({
        title: "Face upload failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    }
  }, [avatarId, ensureAvatarId, onComplete, onFaceReady, layout]);

  // Voice upload handler (separate)
  const handleVoiceUpload = useCallback(async (file: File) => {
    setVoiceStatus("uploading");
    setVoiceFile({ name: file.name, size: file.size });
    try {
      setVoiceStatus("processing");
      const ensuredAvatarId = avatarId || await ensureAvatarId();
      await avatarApi.cloneUploadVoice(ensuredAvatarId, file);
      await qc.invalidateQueries({ queryKey: ["voice-corpus", ensuredAvatarId] });
      // CloneSourcePhase polls the same corpus data under a separate key
      // ("voice-corpus-source") to gate the Continue button. That query's
      // refetchInterval stops polling once it sees zero entries (its first
      // fetch, taken before any voice exists) and never resumes on its own
      // — without invalidating it too, the button stays stuck disabled
      // after upload even though this panel already shows voice ready.
      await qc.invalidateQueries({ queryKey: ["voice-corpus-source", ensuredAvatarId] });
    } catch (err: any) {
      setVoiceStatus("idle");
      setVoiceFile(null);
      toast({
        title: "Voice upload failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    }
  }, [avatarId, ensureAvatarId, qc]);

  // Continue
  const voiceReady = voiceStatus === "ready";
  const faceReady = faceStatus === "ready" && faceCandidates.length > 0;
  const canContinue = faceReady && voiceReady;

  useEffect(() => {
    if (canContinue && !autoCompletedRef.current) {
      autoCompletedRef.current = true;
      onComplete({ candidates: faceCandidates, voiceReady: true, voiceFromFaceVideo });
    }
  }, [canContinue, faceCandidates, voiceFromFaceVideo, onComplete]);

  const showVoiceCard = faceSourceType !== "video" || showVoiceOverride;
  // const [effectiveAvatarId, setEffectiveAvatarId] = useState<string | null>(avatarId ?? null);

  // useEffect(() => {
  //   if (avatarId) {
  //     setEffectiveAvatarId(avatarId);
  //     return;
  //   }
  //   ensureAvatarId().then(setEffectiveAvatarId);
  // }, [avatarId, ensureAvatarId]);

  return (
    <div className="space-y-5">
      {/* Face upload card */}
      <div className="rounded-xl border border-border bg-surface p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Camera className="h-4 w-4 text-accent" />
            <span className="text-sm font-medium text-text">Face</span>
          </div>
          <StatusBadge status={faceStatus} />
        </div>

        <input
          ref={faceInputRef}
          type="file"
          accept="image/*,video/*"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) {
              if (file.type.startsWith("image/")) {
                setPendingCropFile(file);
              } else {
                handleFaceUpload(file);
              }
            }
            e.target.value = "";
          }}
        />

        {pendingCropFile && (
          <ImageCropModal
            file={pendingCropFile}
            targetLayout={layout}
            onCancel={() => setPendingCropFile(null)}
            onConfirm={(croppedFile) => {
              setPendingCropFile(null);
              handleFaceUpload(croppedFile);
            }}
          />
        )}

        {faceStatus === "idle" ? (
          <button
            onClick={() => faceInputRef.current?.click()}
            className="w-full flex flex-col items-center gap-2 rounded-lg border-2 border-dashed border-border p-6 transition-colors hover:border-accent/50 hover:bg-accent/5 cursor-pointer"
          >
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent/10">
              <Upload className="h-5 w-5 text-accent" />
            </div>
            <p className="text-xs text-text-muted">Upload a photo or video of the person</p>
            <p className="text-[10px] text-text-muted">Photo: used directly as face reference</p>
            <p className="text-[10px] text-text-muted">Video: 8 frames extracted + voice auto-extracted</p>
          </button>
        ) : faceStatus === "uploading" || faceStatus === "processing" ? (
          <div className="flex flex-col items-center gap-2 py-6">
            <Loader2 className="h-8 w-8 text-accent animate-spin" />
            <p className="text-xs text-text-muted">
              {faceStatus === "uploading" ? "Uploading..." : "Detecting faces..."}
            </p>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            {faceFile?.thumbUrl ? (
              <div className="h-16 w-16 rounded-lg overflow-hidden border border-border shrink-0">
                <img src={faceFile.thumbUrl} alt="Uploaded" className="h-full w-full object-cover" />
              </div>
            ) : faceCandidates.length > 0 ? (
              <div className="h-16 w-16 rounded-lg overflow-hidden border border-border shrink-0">
                <img src={faceCandidates[0].url} alt="Face" className="h-full w-full object-cover" />
              </div>
            ) : (
              <div className="h-16 w-16 rounded-lg overflow-hidden border border-border shrink-0 bg-surface flex items-center justify-center">
                <ImageIcon className="h-5 w-5 text-text-muted" />
              </div>
            )}
            <div className="flex-1 min-w-0">
              {faceFile && (
                <>
                  <p className="text-xs text-text truncate" title={faceFile.name}>{faceFile.name}</p>
                  <p className="text-[10px] text-text-muted">{formatFileSize(faceFile.size)}</p>
                </>
              )}
              <p className="text-[10px] text-text-muted">
                {faceCandidates.length} face{faceCandidates.length !== 1 ? "s" : ""} detected
              </p>
              {faceSourceType === "video" && voiceFromFaceVideo && (
                <p className="text-[10px] text-accent">Voice extracting from video</p>
              )}
            </div>
            <button
              onClick={() => { setFaceFile(null); setFaceStatus("idle"); setFaceCandidates([]); setFaceSourceType(null); faceInputRef.current?.click(); }}
              className="text-xs text-accent hover:underline shrink-0"
            >
              Re-upload
            </button>
          </div>
        )}
      </div>

      {/* Voice card — conditional on face source */}
      {faceSourceType === "video" && !showVoiceOverride && (
        <div className="rounded-xl border border-border bg-surface p-3">
          <div className="flex items-center gap-2">
            <Mic className="h-4 w-4 text-accent" />
            <span className="text-xs text-text-muted flex-1">
              Voice will be extracted from this video.
            </span>
            <button
              onClick={() => setShowVoiceOverride(true)}
              className="text-xs text-accent hover:underline"
            >
              Upload separate voice file
            </button>
          </div>
        </div>
      )}

      {showVoiceCard && (
        <div className="rounded-xl border border-border bg-surface p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Mic className="h-4 w-4 text-accent" />
              <span className="text-sm font-medium text-text">Voice</span>
            </div>
            <StatusBadge status={voiceStatus} />
          </div>

          <input
            ref={voiceInputRef}
            type="file"
            accept="audio/*,video/*,.mp3,.wav,.m4a,.mp4,.mov,.webm"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleVoiceUpload(file);
              e.target.value = "";
            }}
          />

          {voiceStatus === "idle" || voiceStatus === "failed" ? (
            <VoiceCorpusTab
              avatarId={avatarId ?? null}
              ensureAvatarId={ensureAvatarId}
              compact
              onTrained={() => setVoiceStatus("ready")}
            />
          ) : voiceStatus === "uploading" || voiceStatus === "processing" ? (
            <div className="flex flex-col items-center gap-2 py-4">
              <Loader2 className="h-6 w-6 text-accent animate-spin" />
              <p className="text-xs text-text-muted">
                {voiceStatus === "uploading" ? "Uploading..." : "Processing voice..."}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <CheckCircle className="h-4 w-4 text-green-400" />
                <span className="text-xs text-green-400">Voice ready</span>
                {/* <button
                  onClick={() => voiceInputRef.current?.click()}
                  className="text-xs text-accent hover:underline ml-auto"
                >
                  Add more
                </button> */}
              </div>
              {voiceFile && (
                <div className="flex items-center gap-2 text-[10px] text-text-muted">
                  <FileAudio className="h-3 w-3" />
                  <span className="truncate" title={voiceFile.name}>{voiceFile.name}</span>
                  <span className="shrink-0">({formatFileSize(voiceFile.size)})</span>
                </div>
              )}
              {avatarId && <VoiceCorpusTab avatarId={avatarId} ensureAvatarId={ensureAvatarId} compact />}
            </div>
          )}
        </div>
      )}

     
    </div>
  );
}


// ── Record Sub-Phase ──

function CloneRecordSubPhase({
  avatarId,
  ensureAvatarId,
  onComplete,
  layout,
}: {
  avatarId?: string | null;
  ensureAvatarId: () => Promise<string>;
  onComplete: (result: UploadPhaseResult) => void;
  layout?: string;
}) {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [recorder, setRecorder] = useState<MediaRecorder | null>(null);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordingState, setRecordingState] = useState<"idle" | "requesting" | "ready" | "recording" | "recorded" | "uploading">("idle");
  const [cameraError, setCameraError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const playbackRef = useRef<HTMLVideoElement>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [recordedPreviewUrl, setRecordedPreviewUrl] = useState<string | null>(null);

  const stopLivePreview = useCallback(() => {
    stream?.getTracks().forEach((track) => track.stop());
    setStream(null);
    setRecorder(null);
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, [stream]);

  const getPermissionState = useCallback(async (name: "camera" | "microphone") => {
    if (!("permissions" in navigator) || !navigator.permissions?.query) return "unsupported";
    try {
      const status = await navigator.permissions.query({ name: name as PermissionName });
      return status.state;
    } catch {
      return "unsupported";
    }
  }, []);

  // Request camera + mic
  const requestCamera = useCallback(async () => {
    setRecordingState("requesting");
    setCameraError(null);
    stopLivePreview();
    try {
      const [cameraPermission, microphonePermission] = await Promise.all([
        getPermissionState("camera"),
        getPermissionState("microphone"),
      ]);

      if (cameraPermission === "denied" || microphonePermission === "denied") {
        const message = "Camera permission is blocked in your browser. Re-enable Camera for this site, then try again.";
        setCameraError(message);
        setRecordingState("idle");
        toast({
          title: "Permissions blocked",
          description: message,
          variant: "destructive",
        });
        return;
      }

      const s = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: true,
      });
      setStream(s);
      setRecordingState("ready");
    } catch (err: any) {
      const [cameraPermission, microphonePermission] = await Promise.all([
        getPermissionState("camera"),
        getPermissionState("microphone"),
      ]);
      let message = err?.message || "Camera access denied";

      if (err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError") {
        message = cameraPermission === "denied" || microphonePermission === "denied"
          ? "Camera permission is blocked in your browser. Re-enable Camera for this site, then try again."
          : "Camera access was dismissed before the browser could grant permission. Please click Enable Camera again and allow access.";
      } else if (err?.name === "NotFoundError" || err?.name === "DevicesNotFoundError") {
        message = "No camera or microphone was found on this device.";
      }

      setCameraError(message);
      setRecordingState("idle");
      toast({ title: "Camera unavailable", description: message, variant: "destructive" });
    }
  }, [getPermissionState, stopLivePreview]);

  // Cleanup stream on unmount
  useEffect(() => {
    return () => {
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [stream]);

  useEffect(() => {
    if (!recordedBlob) {
      setRecordedPreviewUrl(null);
      return;
    }

    const url = URL.createObjectURL(recordedBlob);
    setRecordedPreviewUrl(url);

    return () => {
      URL.revokeObjectURL(url);
    };
  }, [recordedBlob]);

  useEffect(() => {
    if (!stream) return;
    if (recordingState !== "ready" && recordingState !== "recording") return;

    const video = videoRef.current;
    if (!video) return;

    video.srcObject = stream;

    const startPreview = async () => {
      try {
        await video.play();
      } catch (err: any) {
        setCameraError(err?.message || "Unable to start camera preview");
      }
    };

    if (video.readyState >= 1) {
      void startPreview();
      return;
    }

    const handleLoadedMetadata = () => {
      void startPreview();
    };

    video.addEventListener("loadedmetadata", handleLoadedMetadata, { once: true });
    return () => {
      video.removeEventListener("loadedmetadata", handleLoadedMetadata);
    };
  }, [stream, recordingState]);

  const startRecording = useCallback(() => {
    if (!stream) return;
    chunksRef.current = [];
    const mimeType = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? "video/webm;codecs=vp9"
      : MediaRecorder.isTypeSupported("video/webm")
        ? "video/webm"
        : "video/mp4";
    const r = new MediaRecorder(stream, { mimeType });
    r.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    r.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mimeType });
      setRecordedBlob(blob);
      setRecordingState("recorded");
    };
    r.start(1000);
    setRecorder(r);
    setRecordingState("recording");
  }, [stream]);

  const stopRecording = useCallback(() => {
    recorder?.stop();
  }, [recorder]);

  const redoRecording = useCallback(() => {
    setRecordedBlob(null);
    setRecordingState("ready");
  }, [stream]);

  const acceptRecording = useCallback(async () => {
    if (!recordedBlob) return;
    setRecordingState("uploading");
    try {
      const ext = recordedBlob.type.includes("mp4") ? "mp4" : "webm";
      const file = new File([recordedBlob], `recording.${ext}`, { type: recordedBlob.type });
      const ensuredAvatarId = avatarId || await ensureAvatarId();
      const result = await avatarApi.cloneUploadFace(ensuredAvatarId, file, true, layout);
      stopLivePreview();
      setRecordingState("recorded");
      onComplete({
        candidates: result.candidates,
        voiceReady: false,
        voiceFromFaceVideo: true,
        avatarId: ensuredAvatarId,
      });
    } catch (err: any) {
      setRecordingState("recorded");
      toast({
        title: "Upload failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    }
  }, [avatarId, ensureAvatarId, recordedBlob, onComplete, stopLivePreview, layout]);

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-border bg-surface p-4 space-y-4">
        <div className="flex items-center gap-2">
          <Video className="h-4 w-4 text-accent" />
          <span className="text-sm font-medium text-text">Record Video</span>
        </div>

        {/* Camera preview / playback area */}
        <div className="aspect-4/3 rounded-lg overflow-hidden bg-black relative">
          {recordingState === "idle" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
              <CameraIcon className="h-10 w-10 text-text-muted" />
              <p className="text-xs text-text-muted">Camera preview will appear here</p>
              {cameraError && <p className="text-xs text-red-400">{cameraError}</p>}
              <Button size="sm" onClick={requestCamera}>
                Enable Camera
              </Button>
            </div>
          )}

          {recordingState === "requesting" && (
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 className="h-8 w-8 text-accent animate-spin" />
            </div>
          )}

          {(recordingState === "ready" || recordingState === "recording") && (
            <video
              ref={videoRef}
              autoPlay
              muted
              playsInline
              className="h-full w-full object-cover mirror"
              style={{ transform: "scaleX(-1)" }}
            />
          )}

          {recordingState === "recorded" && recordedPreviewUrl && (
            <video
              ref={playbackRef}
              src={recordedPreviewUrl}
              controls
              playsInline
              className="h-full w-full object-cover"
            />
          )}

          {recordingState === "uploading" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/80">
              <Loader2 className="h-8 w-8 text-accent animate-spin" />
              <p className="text-xs text-white">Uploading & processing...</p>
            </div>
          )}

          {/* Recording indicator */}
          {recordingState === "recording" && (
            <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-red-600 rounded-full px-2.5 py-1">
              <div className="h-2 w-2 rounded-full bg-white animate-pulse" />
              <span className="text-[10px] text-white font-medium">REC</span>
            </div>
          )}
        </div>

        {/* Controls */}
        <div className="flex items-center justify-center gap-3">
          {recordingState === "ready" && (
            <Button onClick={startRecording} size="lg" className="gap-2">
              <div className="h-3 w-3 rounded-full bg-red-500" />
              Start Recording
            </Button>
          )}
          {recordingState === "recording" && (
            <Button onClick={stopRecording} variant="outline" size="lg" className="gap-2">
              <StopCircle className="h-4 w-4" />
              Stop Recording
            </Button>
          )}
          {recordingState === "recorded" && (
            <>
              <Button onClick={redoRecording} variant="outline" size="sm" className="gap-1.5">
                <RotateCcw className="h-3.5 w-3.5" /> Redo
              </Button>
              <Button onClick={acceptRecording} size="lg" className="gap-2">
                <ArrowRight className="h-4 w-4" /> Use This Recording
              </Button>
            </>
          )}
        </div>

        <p className="text-[10px] text-text-muted text-center">
          Record at least 15 seconds. Both face snapshots and voice will be extracted from the recording.
        </p>
      </div>
    </div>
  );
}


// ── Status Badge ──

function StatusBadge({ status }: { status: "idle" | "uploading" | "processing" | "ready" | "failed" }) {
  if (status === "idle") return null;
  const config = {
    uploading: { label: "Uploading", color: "text-blue-400 bg-blue-500/10 border-blue-500/30" },
    processing: { label: "Processing", color: "text-amber-400 bg-amber-500/10 border-amber-500/30" },
    ready: { label: "Ready", color: "text-green-400 bg-green-500/10 border-green-500/30" },
    failed: { label: "Failed", color: "text-red-400 bg-red-500/10 border-red-500/30" },
  }[status];
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium border", config.color)}>
      {status === "uploading" || status === "processing" ? (
        <span className="flex items-center gap-1">
          <Loader2 className="h-2.5 w-2.5 animate-spin" /> {config.label}
        </span>
      ) : status === "failed" ? (
        <span className="flex items-center gap-1">
          <AlertCircle className="h-2.5 w-2.5" /> {config.label}
        </span>
      ) : (
        <span className="flex items-center gap-1">
          <CheckCircle className="h-2.5 w-2.5" /> {config.label}
        </span>
      )}
    </span>
  );
}


// ── Face Preview Modal ──

function FacePreviewModal({
  candidates,
  currentIdx,
  onSelect,
  onClose,
  onNav,
}: {
  candidates: FaceCandidate[];
  currentIdx: number;
  onSelect: (idx: number) => void;
  onClose: () => void;
  onNav: (idx: number) => void;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onNav(Math.max(0, currentIdx - 1));
      if (e.key === "ArrowRight") onNav(Math.min(candidates.length - 1, currentIdx + 1));
      if (e.key === "Enter") onSelect(currentIdx);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [currentIdx, candidates.length, onSelect, onClose, onNav]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/90" onClick={onClose}>
      <div className="relative max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute -top-10 right-0 text-white/60 hover:text-white">
          <X className="h-6 w-6" />
        </button>

        {currentIdx > 0 && (
          <button
            onClick={() => onNav(currentIdx - 1)}
            className="absolute left-2 top-1/2 -translate-y-1/2 h-10 w-10 rounded-full bg-black/50 flex items-center justify-center text-white/70 hover:text-white z-10"
          >
            <ChevronLeft className="h-5 w-5" />
          </button>
        )}
        {currentIdx < candidates.length - 1 && (
          <button
            onClick={() => onNav(currentIdx + 1)}
            className="absolute right-2 top-1/2 -translate-y-1/2 h-10 w-10 rounded-full bg-black/50 flex items-center justify-center text-white/70 hover:text-white z-10"
          >
            <ChevronRight className="h-5 w-5" />
          </button>
        )}

        <img
          src={candidates[currentIdx].url}
          alt={`Face #${currentIdx + 1}`}
          className="w-full rounded-xl"
        />

        <div className="flex items-center justify-between mt-3">
          <span className="text-sm text-white/60">
            Face {currentIdx + 1} of {candidates.length}
          </span>
          <Button size="sm" onClick={() => onSelect(currentIdx)}>
            Select this face
          </Button>
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════
// PHASE 1 — CloneSourcePhase (merged upload + face select + identity)
// ═══════════════════════════════════════════════════════════════════

function CloneSourcePhase({
  avatarId,
  ensureAvatarId,
  initialData,
  onComplete,
  onLiveChange,
}: {
  avatarId?: string | null;
  ensureAvatarId: () => Promise<string>;
  initialData?: SourcePhaseData | null;
  onComplete: (data: SourcePhaseData) => void;
  onLiveChange?: (data: SourcePhaseData) => void;
}) {
  const [method, setMethod] = useState<UploadMethod>("upload");

  // Candidates from upload sub-phases
  const [candidates, setCandidates] = useState<FaceCandidate[]>([]);
  const [voiceFromFaceVideo, setVoiceFromFaceVideo] = useState(false);
  const [uploadDone, setUploadDone] = useState(false);

  // Face selection (inline, previously phase 2)
  const [selectedFaceIdx, setSelectedFaceIdx] = useState<number | null>(null);
  const [previewIdx, setPreviewIdx] = useState<number | null>(null);

  // Identity fields
  const [name, setName] = useState("");
  const [gender, setGender] = useState("");
  const [voicePreviewText, setVoicePreviewText] = useState(DEFAULT_PREVIEW_TEXT);
  // Layout this avatar's face photo gets cropped/framed for — picked
  // up front, before upload, so the server-side framing step
  // (detect_and_frame_face) uses the right target shape from the start
  // instead of needing a render-time conform fallback later.
  const [layout, setLayout] = useState("9:16");

  // Auto-describe on face selection
  const [describing, setDescribing] = useState(false);
  const [describeError, setDescribeError] = useState<string | null>(null);

  const hydratedRef = useRef(false);

  const PLACEHOLDER_NAMES = new Set(["Clone Avatar", "Clone from TikTok", "AI Avatar", "AI Character"]);

  useEffect(() => {
    if (!initialData || hydratedRef.current) return;
    setCandidates(initialData.candidates || []);
    setSelectedFaceIdx(initialData.selectedFaceIdx ?? null);
    const hydratedName = initialData.name && !PLACEHOLDER_NAMES.has(initialData.name)
      ? initialData.name
      : "";
    setName(hydratedName);
    setGender(initialData.gender || "");
    setVoicePreviewText(initialData.voicePreviewText || DEFAULT_PREVIEW_TEXT);
    setLayout(initialData.layout || "9:16");
    if (initialData.candidates?.length) {
      setUploadDone(true);
    }
    hydratedRef.current = true;
  }, [initialData]);

  // Debounce-save partial input to backend every 2s. Also fires on a
  // non-default layout pick even before a name is typed — the Clone
  // upload step reads avatar.layout synchronously at upload time, so a
  // layout chosen before the first name keystroke still needs to reach
  // the backend before that upload happens.
  const partialSaveRef = useRef<NodeJS.Timeout | null>(null);
  useEffect(() => {
    if (!name.trim() && !avatarId && layout === "9:16") return;
    if (partialSaveRef.current) clearTimeout(partialSaveRef.current);
    partialSaveRef.current = setTimeout(() => {
      void (async () => {
        try {
          const ensuredAvatarId = avatarId || await ensureAvatarId();
          await avatarApi.updateAvatar(ensuredAvatarId, {
            name: name.trim() || undefined,
            gender: gender || undefined,
            layout,
            wizard_step: "source",
          });
        } catch {
          // best-effort autosave
        }
      })();
    }, 2000);
    return () => { if (partialSaveRef.current) clearTimeout(partialSaveRef.current); };
  }, [avatarId, ensureAvatarId, name, gender, voicePreviewText, layout]);

  // Poll real voice-corpus state — the single source of truth for whether a
  // usable voice recording exists, regardless of whether it came from the
  // upload sub-phase, a resumed/hydrated draft, or face-video extraction.
  // (Previously this fell back to `uploadDone`, which only reflects that the
  // FACE step finished — a resumed draft with face candidates but no voice
  // would incorrectly read as voice-ready and let you skip recording audio.)
  const { data: corpusData } = useQuery({
    queryKey: ["voice-corpus-source", avatarId],
    queryFn: () => voiceCorpusApi.list(avatarId!),
    enabled: !!avatarId,
    refetchInterval: (q) => {
      const entries: VoiceCorpusEntry[] = q.state.data?.entries || [];
      const hasReady = entries.some((e) => e.status === "ready");
      if (hasReady) return false;
      // An empty list doesn't mean "nothing pending" — it can just mean the
      // upload's corpus row hasn't landed on the backend yet (the same race
      // the invalidateQueries call above tries to cover). Keep polling on an
      // empty snapshot too, otherwise this can catch that gap and stop
      // forever, leaving voiceReady permanently false.
      const stillWaiting = entries.length === 0 || entries.some((e) => e.status === "pending" || e.status === "processing");
      return stillWaiting ? 5000 : false;
    },
  });

  const voiceReady = (corpusData?.entries || []).some((e: VoiceCorpusEntry) => e.status === "ready");
  const voiceFailed =
    voiceFromFaceVideo &&
    !voiceReady &&
    (corpusData?.entries || []).some((e: VoiceCorpusEntry) => e.status === "failed") &&
    !(corpusData?.entries || []).some((e: VoiceCorpusEntry) => e.status === "pending" || e.status === "processing");

  const handleFaceReady = useCallback(async (faceCandidates: FaceCandidate[], ensuredAvatarIdFromUpload?: string) => {
    setCandidates(faceCandidates);
    if (faceCandidates.length === 1) {
      setSelectedFaceIdx(0);
      const candidate = faceCandidates[0];
      const ensuredAvatarId = ensuredAvatarIdFromUpload || avatarId || await ensureAvatarId();
      try {
        await avatarApi.cloneSelectFace(ensuredAvatarId, candidate.url, candidate.r2_key);
      } catch (err: any) {
        console.warn("Auto-select face failed:", err);
      }
      if (name.trim()) return;
      setDescribing(true);
      try {
        const descResult = await avatarApi.cloneDescribeFace(ensuredAvatarId, candidate.url);
        if (descResult.name) setName(descResult.name);
      } catch { /* non-critical */ }
      setDescribing(false);
    }
  }, [avatarId, ensureAvatarId, name]);
  // When upload sub-phase completes, store candidates and auto-select + persist face
  const handleUploadComplete = useCallback(async (result: UploadPhaseResult) => {
    setCandidates(result.candidates);
    setVoiceFromFaceVideo(result.voiceFromFaceVideo);
    setUploadDone(true);
    if (result.candidates.length === 1) {
      setSelectedFaceIdx(0);
      const candidate = result.candidates[0];
      const ensuredAvatarId = result.avatarId || avatarId || await ensureAvatarId();
      try {
        await avatarApi.cloneSelectFace(ensuredAvatarId, candidate.url, candidate.r2_key);
      } catch (err: any) {
        console.warn("Auto-select face failed:", err);
      }
      if (name.trim()) return;
      setDescribing(true);
      try {
        const descResult = await avatarApi.cloneDescribeFace(ensuredAvatarId, candidate.url);
        if (descResult.name) setName(descResult.name);
      } catch { /* non-critical */ }
      setDescribing(false);
    }
  }, [avatarId, ensureAvatarId, name]);

  const regenerateIdentity = useCallback(async () => {
    if (selectedFaceIdx === null || !candidates[selectedFaceIdx]) return;
    setDescribing(true);
    setDescribeError(null);
    try {
      const ensuredAvatarId = avatarId || await ensureAvatarId();
      const descResult = await avatarApi.cloneDescribeFace(ensuredAvatarId, candidates[selectedFaceIdx].url);
      if (descResult.name) setName(descResult.name);
    } catch (err: any) {
      setDescribeError("Couldn't generate a name — try again.");
      toast({
        title: "Name generation failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    } finally {
      setDescribing(false);
    }
  }, [avatarId, ensureAvatarId, selectedFaceIdx, candidates]);
  // Select face on backend + auto-describe when user clicks a face
  const handleFaceSelect = useCallback(async (idx: number) => {
    setSelectedFaceIdx(idx);
    if (!candidates[idx]) return;

    // Select face on backend — persist face_ref_key
    const candidate = candidates[idx];
    try {
      const ensuredAvatarId = avatarId || await ensureAvatarId();
      await avatarApi.cloneSelectFace(ensuredAvatarId, candidate.url, candidate.r2_key);
    } catch (err: any) {
      console.warn("Select face failed:", err);
      toast({
        title: "Face selection may not have saved",
        description: "Will retry when you continue.",
        variant: "destructive",
      });
    }

    // Auto-describe
    setDescribing(true);
    try {
      const ensuredAvatarId = avatarId || await ensureAvatarId();
      const descResult = await avatarApi.cloneDescribeFace(ensuredAvatarId, candidates[idx].url);
      if (descResult.name && !name) setName(descResult.name);
    } catch { /* non-critical */ }
    setDescribing(false);
  }, [avatarId, ensureAvatarId, candidates, name]);

  // Validation
  const canContinue =
    !!name.trim() &&
    !!gender &&
    !!voicePreviewText.trim() &&
    selectedFaceIdx !== null &&
    voiceReady &&
    uploadDone;

  const [continuing, setContinuing] = useState(false);

  const handleContinue = useCallback(async () => {
    if (!canContinue || selectedFaceIdx === null) return;
    setContinuing(true);
    try {
      // Ensure face_ref_key is persisted on backend before advancing
      const candidate = candidates[selectedFaceIdx];
      if (candidate) {
        try {
          const ensuredAvatarId = avatarId || await ensureAvatarId();
          await avatarApi.cloneSelectFace(ensuredAvatarId, candidate.url, candidate.r2_key);
        } catch (err: any) {
          console.warn("Select face on continue failed:", err);
        }
      }
      onComplete({
        name: name.trim(),
        gender,
        voicePreviewText: voicePreviewText.trim(),
        selectedFaceIdx,
        candidates,
        layout,
      });
    } finally {
      setContinuing(false);
    }
  }, [canContinue, avatarId, ensureAvatarId, name, gender, voicePreviewText, selectedFaceIdx, candidates, layout, onComplete]);

  // Keep the parent's sourceData snapshot live, not just on "Continue" — the
  // Source step stays mounted (only CSS-hidden) when navigating away via
  // Back/Forward/step-indicator, so editing e.g. voicePreviewText and then
  // jumping straight to Preview without re-clicking Continue would otherwise
  // regenerate against the stale text captured at the last Continue click.
  useEffect(() => {
    if (!hydratedRef.current || selectedFaceIdx === null) return;
    onLiveChange?.({
      name: name.trim(),
      gender,
      voicePreviewText: voicePreviewText.trim(),
      selectedFaceIdx,
      candidates,
      layout,
    });
  }, [name, gender, voicePreviewText, selectedFaceIdx, candidates, layout, onLiveChange]);

  return (
    <div className="space-y-6" data-testid="clone-source-phase">
      {/* Layout — picked before upload so the server-side face framing
          crops/fits to the right shape from the start. */}
      <div className="space-y-1.5">
        <h3 className="text-sm font-semibold text-text">Layout</h3>
        <p className="text-xs text-text-muted">
          Which cast layout will this avatar mostly be used for? Only casts
          in the same layout will show this avatar.
        </p>
        <div className="grid grid-cols-4 gap-1.5 max-w-md">
          {LAYOUT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setLayout(opt.value)}
              title={`${opt.label} — ${opt.desc}`}
              className={cn(
                "rounded-lg border p-2 text-center transition-all",
                layout === opt.value
                  ? "border-accent bg-accent/10"
                  : "border-border bg-surface hover:border-accent/40",
              )}
            >
              <div className="text-base leading-none">{opt.icon}</div>
              <div className="text-[10px] font-medium text-text mt-0.5">{opt.label}</div>
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ── LEFT COLUMN: Upload controls ── */}
        <div className="space-y-5">
          <h3 className="text-sm font-semibold text-text">Upload Source</h3>

          {/* Method picker */}
          <div className="flex gap-2 p-1 bg-surface rounded-lg border border-border">
            <button
              onClick={() => setMethod("social")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 rounded-md px-3 py-2.5 text-xs font-medium transition-all",
                method === "social"
                  ? "bg-accent text-white shadow-xs"
                  : "text-text-muted hover:text-text hover:bg-surface-hover",
              )}
            >
              <Globe className="h-4 w-4" /> Social Media
            </button>
            <button
              onClick={() => setMethod("upload")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 rounded-md px-3 py-2.5 text-xs font-medium transition-all",
                method === "upload"
                  ? "bg-accent text-white shadow-xs"
                  : "text-text-muted hover:text-text hover:bg-surface-hover",
              )}
            >
              <Upload className="h-4 w-4" /> Upload
            </button>
            <button
              onClick={() => setMethod("record")}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 rounded-md px-3 py-2.5 text-xs font-medium transition-all",
                method === "record"
                  ? "bg-accent text-white shadow-xs"
                  : "text-text-muted hover:text-text hover:bg-surface-hover",
              )}
            >
              <Video className="h-4 w-4" /> Record
            </button>
          </div>

          {/* Sub-phase content */}
          {method === "social" && (
            <CloneSocialMediaSubPhase avatarId={avatarId} ensureAvatarId={ensureAvatarId} onComplete={handleUploadComplete} />
          )}
          {method === "upload" && (
            <CloneUploadSubPhase avatarId={avatarId} ensureAvatarId={ensureAvatarId} onComplete={handleUploadComplete} onFaceReady={handleFaceReady} existingFace={candidates[selectedFaceIdx ?? 0] ?? null} layout={layout} />
          )}
          {method === "record" && (
            <CloneRecordSubPhase avatarId={avatarId} ensureAvatarId={ensureAvatarId} onComplete={handleUploadComplete} layout={layout} />
          )}
        </div>

        {/* ── RIGHT COLUMN: Face candidates + identity fields ── */}
        <div className="space-y-5">
          {/* Face candidates grid */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-text">Face Selection</h3>
              {voiceFromFaceVideo && uploadDone && !voiceReady && !voiceFailed && (
                <span className="flex items-center gap-1.5 text-xs text-amber-400">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Voice processing...
                </span>
              )}
              {voiceFromFaceVideo && voiceFailed && (
                <span className="flex items-center gap-1.5 text-xs text-red-400">
                  <AlertCircle className="h-3 w-3" />
                  Voice extraction failed
                </span>
              )}
              {voiceFromFaceVideo && voiceReady && (
                <span className="flex items-center gap-1.5 text-xs text-green-400">
                  <CheckCircle className="h-3 w-3" />
                  Voice ready
                </span>
              )}
            </div>
            {voiceFailed && avatarId && (
              <div className="space-y-2 rounded-lg border border-red-500/30 bg-red-900/10 p-3">
                <p className="text-xs text-red-400">
                  Couldn't extract a voice from your video. Record or upload one manually instead:
                </p>
                <VoiceCorpusTab avatarId={avatarId} ensureAvatarId={ensureAvatarId} compact />
              </div>
            )}

            {candidates.length === 0 ? (
              <div className="grid grid-cols-4 gap-2">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div
                    key={i}
                    className="aspect-3/4 rounded-lg border border-dashed border-border bg-surface flex items-center justify-center"
                  >
                    <ImageIcon className="h-5 w-5 text-border" />
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-4 gap-2">
                {candidates.map((candidate, i) => (
                  <button
                    key={i}
                    onClick={() => handleFaceSelect(i)}
                    onDoubleClick={() => setPreviewIdx(i)}
                    className={cn(
                      "relative aspect-3/4 rounded-lg overflow-hidden border-2 transition-all",
                      selectedFaceIdx === i
                        ? "border-accent ring-2 ring-accent/30 scale-[1.02]"
                        : "border-border hover:border-accent/40 hover:scale-[1.03]",
                    )}
                  >
                    <img
                      src={candidate.url}
                      alt={`Face #${i + 1}`}
                      className="h-full w-full object-cover"
                    />
                    {selectedFaceIdx === i && (
                      <div className="absolute top-1 right-1 h-5 w-5 rounded-full bg-accent flex items-center justify-center">
                        <CheckCircle className="h-3.5 w-3.5 text-white" />
                      </div>
                    )}
                    <div className="absolute bottom-0 left-0 right-0 bg-linear-to-t from-black/60 to-transparent p-1">
                      <span className="text-[10px] text-white font-medium">#{i + 1}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
            {candidates.length > 0 && (
              <p className="text-[10px] text-text-muted">
                Click to select. Double-click for full-size preview.
              </p>
            )}
          </div>

          {/* Identity fields below face grid */}
          <div className="space-y-4 rounded-xl border border-border bg-surface p-4">
            <h4 className="text-xs font-semibold text-text uppercase tracking-wider">Avatar Identity</h4>

            {/* Avatar name (required) */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-text-muted">
                  Avatar Name <span className="text-red-400">*</span>
                </label>
                <button
                  type="button"
                  onClick={regenerateIdentity}
                  disabled={describing || selectedFaceIdx === null}
                  className="flex items-center gap-1 text-xs text-accent hover:underline disabled:opacity-40 disabled:no-underline"
                >
                  {describing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Shuffle className="h-3 w-3" />}
                  Regenerate
                </button>
              </div>
              <ShimmerField isLoading={describing}>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Enter avatar name..."
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-text placeholder:text-text-muted"
                />
              </ShimmerField>
              {describeError && (
                <p className="text-[10px] text-red-400 mt-1">{describeError}</p>
              )}
            </div>

            {/* Gender select (required) */}
            <div>
              <label className="text-xs text-text-muted mb-1 block">
                Gender <span className="text-red-400">*</span>
              </label>
              <div className="flex flex-wrap gap-2">
                {GENDER_SELECT_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setGender(opt.value)}
                    className={cn(
                      "rounded-lg px-3 py-2 text-xs font-medium transition-all border",
                      gender === opt.value
                        ? "bg-accent text-white border-accent"
                        : "bg-background border-border text-text-muted hover:border-accent/50",
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Voice preview text (multiline, accepts any language) */}
            <div>
              <label className="text-xs text-text-muted mb-1 block">
                Voice Preview Text <span className="text-red-400">*</span>
              </label>
              <textarea
                value={voicePreviewText}
                onChange={(e) => setVoicePreviewText(e.target.value)}
                placeholder="Enter text for the voice preview (any language)..."
                rows={4}
                className="w-full rounded-lg border border-border bg-background p-3 text-xs text-text placeholder:text-text-muted resize-none"
              />
              <p className="text-[10px] text-text-muted mt-1">
                Type in any language. This text will be spoken in the preview video.
              </p>
            </div>

            {/* Language is auto-detected server-side after TTS */}
          </div>
        </div>
      </div>

      {/* Continue button — full width */}
      <Button
        onClick={handleContinue}
        disabled={!canContinue || continuing}
        className="w-full gap-2"
        size="lg"
      >
        {continuing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
        Continue to Audience
      </Button>

      {/* Face preview modal */}
      {previewIdx !== null && candidates[previewIdx] && (
        <FacePreviewModal
          candidates={candidates}
          currentIdx={previewIdx}
          onSelect={(idx) => { handleFaceSelect(idx); setPreviewIdx(null); }}
          onClose={() => setPreviewIdx(null)}
          onNav={setPreviewIdx}
        />
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════
// PHASE 2 — CloneAudiencePhase (redesigned target audience)
// ═══════════════════════════════════════════════════════════════════

function CloneAudiencePhase({
  avatarId,
  sourceData,
  initialData,
  onComplete,
  onBack,
}: {
  avatarId: string;
  sourceData: SourcePhaseData;
  initialData?: AudiencePhaseData | null;
  onComplete: (data: AudiencePhaseData) => void;
  onBack?: () => void;
}) {
  // Age range — dual sliders
  const [ageMin, setAgeMin] = useState(18);
  const [ageMax, setAgeMax] = useState(45);

  // Gender lean dial
  const [genderLean, setGenderLean] = useState(50);
  const [genderDoesntMatter, setGenderDoesntMatter] = useState(false);

  // Interests — selected on THIS avatar.
  const [interests, setInterests] = useState<string[]>([]);
  const [customInterest, setCustomInterest] = useState("");
  // userInterests = the list of custom presets persisted on the User
  // record. Adding one here makes it available as a clickable preset on
  // every future avatar this user creates. Stored as lowercase server-
  // side; we capitalize for display.
  const [userInterests, setUserInterests] = useState<string[]>([]);

  // Geography / market
  const [geography, setGeography] = useState("");

  // Income bracket
  const [incomeBracket, setIncomeBracket] = useState("");

  // Occupation / niche
  const [occupations, setOccupations] = useState<string[]>([]);
  const [customOccupation, setCustomOccupation] = useState("");

  // Auto-generated audience description
  const [audienceDesc, setAudienceDesc] = useState("");
  const [audienceDescOverridden, setAudienceDescOverridden] = useState(false);
  const [isGeneratingAudience, setIsGeneratingAudience] = useState(false);

  // Saving
  const [saving, setSaving] = useState(false);

  // Debounce ref
  const audienceDebounceRef = useRef<NodeJS.Timeout | null>(null);
  const autosaveRef = useRef<NodeJS.Timeout | null>(null);
  const hydratedRef = useRef(false);

  // Computed age range string for API
  const ageRangeString = `${ageMin}-${ageMax >= 65 ? "65+" : ageMax}`;

  // Gender lean label
  const genderLeanLabel = genderDoesntMatter
    ? "Any"
    : genderLean <= 15
      ? "Strongly female"
      : genderLean <= 35
        ? "Leaning female"
        : genderLean <= 65
          ? "Neutral"
          : genderLean <= 85
            ? "Leaning male"
            : "Strongly male";

  useEffect(() => {
    if (!initialData || hydratedRef.current) return;
    setAgeMin(initialData.ageMin);
    setAgeMax(initialData.ageMax);
    setGenderLean(initialData.genderLean);
    setGenderDoesntMatter(initialData.genderDoesntMatter);
    setInterests(initialData.interests);
    setGeography(initialData.geography);
    setIncomeBracket(initialData.incomeBracket);
    setOccupations(initialData.occupations);
    setAudienceDesc(initialData.audienceDesc);
    hydratedRef.current = true;
  }, [initialData]);

  // Autosave on idle
  const triggerAutosave = useCallback(() => {
    if (autosaveRef.current) clearTimeout(autosaveRef.current);
    autosaveRef.current = setTimeout(() => {
      avatarApi.updateAvatar(avatarId, {
        name: sourceData.name || undefined,
        gender: sourceData.gender || undefined,
        target_audience: {
          age_range: ageRangeString,
          age_min: ageMin,
          age_max: ageMax,
          gender_lean: genderDoesntMatter ? "any" : genderLean,
          interests,
          geography,
          income_bracket: incomeBracket,
          occupations,
          description: audienceDesc,
        },
      }).catch(() => { });
    }, 2000);
  }, [avatarId, sourceData, ageRangeString, ageMin, ageMax, genderLean, genderDoesntMatter, interests, geography, incomeBracket, occupations, audienceDesc]);

  useEffect(() => {
    if (interests.length === 0 && !geography && !incomeBracket) return;
    triggerAutosave();
    return () => { if (autosaveRef.current) clearTimeout(autosaveRef.current); };
  }, [ageMin, ageMax, genderLean, genderDoesntMatter, interests, geography, incomeBracket, occupations, audienceDesc, triggerAutosave]);

  // Generate audience description function (called by button)
  const generateAudienceDescription = useCallback(async () => {
    setIsGeneratingAudience(true);
    try {
      const result = await avatarApi.aiRewriteAudienceDescription({
        age_min: ageMin,
        age_max: ageMax,
        gender_lean: genderLean,
        gender_doesnt_matter: genderDoesntMatter,
        interests,
        geography,
        income_bracket: incomeBracket,
        occupations,
      });
      setAudienceDesc(result.description);
      setAudienceDescOverridden(false);
    } catch { /* ignore */ }
    setIsGeneratingAudience(false);
  }, [ageMin, ageMax, genderLean, genderDoesntMatter, interests, geography, incomeBracket, occupations]);

  // Interest toggles
  const toggleInterest = useCallback((interest: string) => {
    setInterests((prev) =>
      prev.includes(interest) ? prev.filter((i) => i !== interest) : [...prev, interest]
    );
  }, []);

  // Load saved custom presets ONCE on mount so they appear as clickable
  // chips alongside INTEREST_OPTIONS. Quietly tolerates failures — the
  // page still works without persisted presets.
  useEffect(() => {
    let cancelled = false;
    userApi.getInterests().then((list) => {
      if (!cancelled) setUserInterests(list);
    }).catch(() => { /* offline or unauth, ignore */ });
    return () => { cancelled = true; };
  }, []);

  const addCustomInterest = useCallback(async () => {
    const trimmed = customInterest.trim();
    if (!trimmed) return;
    setCustomInterest("");
    // Always select on this avatar (even if already there — the toggle
    // logic below treats already-included as no-op).
    setInterests((prev) =>
      prev.includes(trimmed) ? prev : [...prev, trimmed]
    );
    // Persist as a user preset so the chip survives across future avatars.
    // We compare case-insensitively against the existing presets to avoid
    // creating 'Vegan' AND 'vegan' AND 'VEGAN' as separate entries.
    const lower = trimmed.toLowerCase();
    const alreadyPreset = userInterests.some((p) => p.toLowerCase() === lower);
    if (!alreadyPreset) {
      const next = [...userInterests, trimmed];
      // Optimistic local update so the chip appears immediately.
      setUserInterests(next);
      try {
        const saved = await userApi.setInterests(next);
        // Server normalizes (lowercases, dedupes). Reflect that.
        setUserInterests(saved);
      } catch (err) {
        // Roll back local change on failure so the next page load is clean.
        setUserInterests(userInterests);
        toast({ title: "Couldn't save custom interest", variant: "destructive" });
      }
    }
  }, [customInterest, interests, userInterests]);

  // Remove a saved preset entirely — takes it out of both this avatar's
  // selection and the user's persistent list.
  const removeUserInterest = useCallback(async (label: string) => {
    const lower = label.toLowerCase();
    const next = userInterests.filter((p) => p.toLowerCase() !== lower);
    setUserInterests(next);
    setInterests((prev) => prev.filter((i) => i.toLowerCase() !== lower));
    try {
      await userApi.setInterests(next);
    } catch {
      // Best-effort; stale UI on failure recovers on next refresh.
    }
  }, [userInterests]);

  // Occupation toggles
  const toggleOccupation = useCallback((occ: string) => {
    setOccupations((prev) =>
      prev.includes(occ) ? prev.filter((o) => o !== occ) : [...prev, occ]
    );
  }, []);

  const addCustomOccupation = useCallback(() => {
    const trimmed = customOccupation.trim();
    if (trimmed && !occupations.includes(trimmed)) {
      setOccupations((prev) => [...prev, trimmed]);
    }
    setCustomOccupation("");
  }, [customOccupation, occupations]);

  // Save + continue
  const handleContinue = useCallback(async () => {
    setSaving(true);
    try {
      await avatarApi.aiSaveSetup(avatarId, {
        name: sourceData.name,
        target_audience: {
          age_range: ageRangeString,
          age_min: ageMin,
          age_max: ageMax,
          gender_lean: genderDoesntMatter ? "any" : genderLean,
          interests,
          geography,
          income_bracket: incomeBracket,
          occupations,
          description: audienceDesc,
        },
        gender: sourceData.gender,
        description: audienceDesc,
      });

      onComplete({
        ageMin,
        ageMax,
        genderLean,
        genderDoesntMatter,
        interests,
        geography,
        incomeBracket,
        occupations,
        audienceDesc,
      });
    } catch (err: any) {
      toast({
        title: "Failed to save audience",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  }, [avatarId, sourceData, ageRangeString, ageMin, ageMax, genderLean, genderDoesntMatter, interests, geography, incomeBracket, occupations, audienceDesc, onComplete]);

  return (
    <div className="space-y-8" data-testid="clone-audience-phase">
      {/* ── Age Range — dual sliders ── */}
      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <Sliders className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-semibold text-text">Age Range</h3>
          <span className="ml-auto text-xs text-accent font-medium">
            {ageMin} &ndash; {ageMax >= 65 ? "65+" : ageMax}
          </span>
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <label className="text-xs text-text-muted w-10">Min</label>
            <input
              type="range"
              min={13}
              max={65}
              step={1}
              value={ageMin}
              onChange={(e) => {
                const v = parseInt(e.target.value);
                setAgeMin(v);
                if (ageMax < v) setAgeMax(v);
              }}
              className="flex-1 accent-accent"
            />
            <span className="text-xs text-text-muted w-8 text-right">{ageMin}</span>
          </div>
          <div className="flex items-center gap-3">
            <label className="text-xs text-text-muted w-10">Max</label>
            <input
              type="range"
              min={13}
              max={65}
              step={1}
              value={ageMax}
              onChange={(e) => {
                const v = parseInt(e.target.value);
                setAgeMax(v);
                if (ageMin > v) setAgeMin(v);
              }}
              className="flex-1 accent-accent"
            />
            <span className="text-xs text-text-muted w-8 text-right">{ageMax >= 65 ? "65+" : ageMax}</span>
          </div>
        </div>
      </section>

      {/* ── Gender Lean Dial ── */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold text-text">Audience Gender Lean</h3>

        {!genderDoesntMatter && (
          <div className="space-y-2">
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={genderLean}
              onChange={(e) => setGenderLean(parseInt(e.target.value))}
              className="w-full accent-accent"
            />
            <div className="flex justify-between text-[10px] text-text-muted">
              <span>Strongly female</span>
              <span>Neutral</span>
              <span>Strongly male</span>
            </div>
            <p className="text-xs text-accent text-center font-medium">{genderLeanLabel}</p>
          </div>
        )}

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={genderDoesntMatter}
            onChange={(e) => setGenderDoesntMatter(e.target.checked)}
            className="rounded border-border text-accent focus:ring-accent"
          />
          <span className="text-xs text-text-muted">Gender doesn't matter</span>
        </label>
      </section>

      {/* ── Interests ── */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold text-text">Interests</h3>
        {/* Built-in presets, then the user's saved custom presets. We render
            them in the same row so the user can't tell which were typed by
            them — except that custom ones get a small × to remove. The toggle
            click works identically for both. Case-insensitive de-dupe so a
            built-in 'Fitness' and a typed 'fitness' don't both appear. */}
        <div className="flex flex-wrap gap-1.5">
          {INTEREST_OPTIONS.map((interest) => (
            <button
              key={interest}
              onClick={() => toggleInterest(interest)}
              className={cn(
                "rounded-full px-2.5 py-1 text-[11px] font-medium transition-all border",
                interests.includes(interest)
                  ? "bg-accent/20 text-accent border-accent/40"
                  : "bg-surface border-border text-text-muted hover:border-accent/30",
              )}
            >
              {interest}
            </button>
          ))}
          {userInterests
            .filter((p) => !INTEREST_OPTIONS.some((b) => b.toLowerCase() === p.toLowerCase()))
            .map((interest) => {
              const selected = interests.some((i) => i.toLowerCase() === interest.toLowerCase());
              return (
                <span
                  key={`u-${interest}`}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full pl-2.5 pr-1.5 py-1 text-[11px] font-medium transition-all border",
                    selected
                      ? "bg-accent/20 text-accent border-accent/40"
                      : "bg-surface border-border text-text-muted hover:border-accent/30",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => toggleInterest(interest)}
                    className="focus:outline-none"
                  >
                    {interest}
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove ${interest}`}
                    title="Remove from your saved interests"
                    onClick={() => removeUserInterest(interest)}
                    className="ml-0.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-text-muted/70 hover:text-text hover:bg-white/10"
                  >
                    ×
                  </button>
                </span>
              );
            })}
        </div>
        <div className="flex gap-2">
          <input
            value={customInterest}
            onChange={(e) => setCustomInterest(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustomInterest(); } }}
            placeholder="Add custom interest..."
            className="flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs text-text placeholder:text-text-muted"
          />
          <Button size="sm" variant="outline" onClick={addCustomInterest} disabled={!customInterest.trim()}>Add</Button>
        </div>
      </section>

      {/* ── Geography / Market ── */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-text">Geography / Market</h3>
        <select
          value={geography}
          onChange={(e) => setGeography(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
        >
          {GEOGRAPHY_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </section>

      {/* ── Income Bracket ── */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-text">Income Bracket</h3>
        <select
          value={incomeBracket}
          onChange={(e) => setIncomeBracket(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
        >
          {INCOME_BRACKET_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </section>

      {/* ── Occupation / Niche ── */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold text-text">Occupation / Niche</h3>
        <div className="flex flex-wrap gap-1.5">
          {OCCUPATION_OPTIONS.map((occ) => (
            <button
              key={occ}
              onClick={() => toggleOccupation(occ)}
              className={cn(
                "rounded-full px-2.5 py-1 text-[11px] font-medium transition-all border",
                occupations.includes(occ)
                  ? "bg-accent/20 text-accent border-accent/40"
                  : "bg-surface border-border text-text-muted hover:border-accent/30",
              )}
            >
              {occ}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            value={customOccupation}
            onChange={(e) => setCustomOccupation(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addCustomOccupation()}
            placeholder="Add custom occupation..."
            className="flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs text-text placeholder:text-text-muted"
          />
          {customOccupation.trim() && (
            <Button size="sm" variant="outline" onClick={addCustomOccupation}>Add</Button>
          )}
        </div>
      </section>

      {/* ── Audience Description (auto-generated) ── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text">Audience Description</h3>
          <Button
            size="sm"
            variant="outline"
            onClick={generateAudienceDescription}
            disabled={isGeneratingAudience}
          >
            {isGeneratingAudience ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Wand2 className="h-3 w-3" />
            )}
            {isGeneratingAudience ? "Generating..." : "Generate"}
          </Button>
        </div>
        <ShimmerField isLoading={isGeneratingAudience}>
          <textarea
            value={audienceDesc}
            onChange={(e) => { setAudienceDesc(e.target.value); setAudienceDescOverridden(true); }}
            placeholder="Click 'Generate' to create an audience description..."
            rows={3}
            maxLength={500}
            className="w-full rounded-lg border border-border bg-surface p-3 text-xs text-text placeholder:text-text-muted resize-none"
          />
        </ShimmerField>
      </section>

      {/* Navigation buttons */}
      <div className="flex items-center gap-3">
        {onBack && (
          <Button
            onClick={onBack}
            variant="outline"
            size="lg"
            className="gap-2"
          >
            <ChevronLeft className="h-4 w-4" />
            Back
          </Button>
        )}
        <Button
          onClick={handleContinue}
          disabled={saving}
          className="flex-1 gap-2"
          size="lg"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
          {saving ? "Saving..." : "Continue to Preview"}
        </Button>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════
// PHASE 3 — ClonePreviewPhase (summary + generate + approve)
// ═══════════════════════════════════════════════════════════════════

function ClonePreviewPhase({
  avatarId,
  sourceData,
  onBack,
}: {
  avatarId: string;
  sourceData: SourcePhaseData;
  onBack?: () => void;
}) {
  const navigate = useNavigate();
  const [approved, setApproved] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState(false);

  const { data: avatarStatus, refetch: refetchStatus } = useQuery({
    queryKey: ["avatar-status", avatarId],
    queryFn: () => avatarApi.status(avatarId),
    enabled: !!avatarId && generated,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      if (status === "READY" || status === "APPROVED" || status === "FAILED") return false;
      return 3000;
    },
  });

  const isReady = avatarStatus?.status === "READY" || avatarStatus?.status === "APPROVED";
  const isFailed = avatarStatus?.status === "FAILED";
  const testVideoUrl = avatarStatus?.test_video_url;

  // Generate preview
  const handleGenerate = useCallback(async () => {
    setGenerating(true);
    try {
      await avatarApi.cloneGenerate(avatarId, sourceData.voicePreviewText);
      setGenerated(true);
      refetchStatus();
    } catch (err: any) {
      toast({
        title: "Generation failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    } finally {
      setGenerating(false);
    }
  }, [avatarId, sourceData.voicePreviewText, refetchStatus]);

  // Regenerate
  const handleRegenerate = useCallback(async () => {
    setGenerating(true);
    setGenerated(false);
    try {
      await avatarApi.cloneGenerate(avatarId, sourceData.voicePreviewText);
      setGenerated(true);
      refetchStatus();
    } catch (err: any) {
      toast({
        title: "Regeneration failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    } finally {
      setGenerating(false);
    }
  }, [avatarId, sourceData.voicePreviewText, refetchStatus]);

  const approveMutation = useMutation({
    mutationFn: () => avatarApi.approve(avatarId),
    onSuccess: () => {
      setApproved(true);
      toast({ title: "Avatar approved!" });
      setTimeout(() => navigate("/my-avatar"), 1500);
    },
    onError: (err: any) => {
      toast({
        title: "Approval failed",
        description: err?.response?.data?.detail || "Please try again",
        variant: "destructive",
      });
    },
  });

  // Selected face thumbnail
  const selectedFace = sourceData.candidates[sourceData.selectedFaceIdx];

  return (
    <div className="space-y-6" data-testid="clone-preview-phase">
      {/* Summary card */}
      <div className="rounded-xl border border-border bg-surface p-5 space-y-4">
        <h3 className="text-sm font-semibold text-text">Avatar Summary</h3>

        <div className="flex items-start gap-4">
          {/* Face thumbnail */}
          {selectedFace && (
            <div className="h-20 w-20 rounded-lg overflow-hidden border border-border shrink-0">
              <img
                src={selectedFace.url}
                alt="Selected face"
                className="h-full w-full object-cover"
              />
            </div>
          )}

          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs text-text-muted">Name:</span>
              <span className="text-sm font-medium text-text">{sourceData.name}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-text-muted">Gender:</span>
              <span className="text-sm text-text capitalize">
                {GENDER_SELECT_OPTIONS.find((g) => g.value === sourceData.gender)?.label || sourceData.gender}
              </span>
            </div>
            {/* Language detected automatically after generation */}
          </div>
        </div>

        {/* Voice preview text */}
        <div>
          <span className="text-xs text-text-muted block mb-1">Voice Preview Text:</span>
          <div className="rounded-lg border border-border bg-background p-3 text-xs text-text whitespace-pre-wrap">
            {sourceData.voicePreviewText}
          </div>
        </div>
      </div>

      {/* Generate button (before generation) */}
      {!generated && !generating && (
        <div className="flex items-center gap-3">
          {onBack && (
            <Button
              onClick={onBack}
              variant="outline"
              size="lg"
              className="gap-2"
            >
              <ChevronLeft className="h-4 w-4" />
              Back
            </Button>
          )}
          <Button
            onClick={handleGenerate}
            className="flex-1 gap-2"
            size="lg"
          >
            <Wand2 className="h-4 w-4" />
            Generate Preview
          </Button>
        </div>
      )}

      {/* Generating / pipeline progress */}
      {generating && (
        <div className="flex flex-col items-center gap-3 py-6">
          <Loader2 className="h-8 w-8 text-accent animate-spin" />
          <p className="text-sm text-text-muted">Starting generation...</p>
        </div>
      )}

      {generated && !isReady && !isFailed && (
        <>
          <PipelineProgressView
            avatarId={avatarId}
            onComplete={() => { }}
          />
          <div className="flex flex-col items-center gap-3 py-4">
            <Loader2 className="h-8 w-8 text-accent animate-spin" />
            <p className="text-sm text-text-muted">Creating your avatar...</p>
            <p className="text-xs text-text-muted">This usually takes 2-5 minutes</p>
          </div>
        </>
      )}

      {/* Ready — video player + approve/regenerate */}
      {isReady && testVideoUrl && (
        <div className="flex flex-col items-center gap-4">
          <div className="w-full max-w-xs mx-auto rounded-xl overflow-hidden border border-border bg-black">
            <div style={{ aspectRatio: playerAspectRatio(sourceData.layout) }}>
              <video
                src={testVideoUrl}
                controls
                className="h-full w-full object-contain"
              />
            </div>
          </div>

          <div className="flex gap-3">
            <Button
              onClick={handleRegenerate}
              disabled={generating}
              variant="outline"
              size="lg"
              className="gap-2"
            >
              <RotateCcw className="h-4 w-4" />
              Regenerate
            </Button>
            <Button
              onClick={() => approveMutation.mutate()}
              disabled={approveMutation.isPending || approved}
              size="lg"
              className="gap-2"
            >
              {approveMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle className="h-4 w-4" />
              )}
              {approved ? "Approved!" : "Approve Avatar"}
            </Button>
          </div>
        </div>
      )}

      {/* Failed */}
      {isFailed && (
        <div className="text-center py-8">
          <p className="text-sm text-red-400">
            {avatarStatus?.progress_step || "Generation failed. Please try again or contact support."}
          </p>
          <div className="flex gap-3 justify-center mt-3">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRegenerate}
              disabled={generating}
              className="gap-1.5"
            >
              <RotateCcw className="h-3.5 w-3.5" /> Try Again
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate("/my-avatar/clone")}
            >
              Start Over
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════
// MAIN — CloneFlow orchestrator (3-step flow)
// ═══════════════════════════════════════════════════════════════════

export function CloneFlow({ resumeAvatarId, resumeStep }: { resumeAvatarId?: string; resumeStep?: string } = {}) {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const initialResumeId = resumeAvatarId || searchParams.get("resume");
  const isResumeFlow = !!initialResumeId;
  const [phase, setPhase] = useState<ClonePhase>("source");
  const [avatarId, setAvatarId] = useState<string | null>(initialResumeId);
  const [createError, setCreateError] = useState(false);
  const [sourceData, setSourceData] = useState<SourcePhaseData | null>(null);
  const [audienceData, setAudienceData] = useState<AudiencePhaseData | null>(null);
  const resumeHandled = useRef(false);
  const createPromiseRef = useRef<Promise<string> | null>(null);

  // Autosave wizard_step on phase change + update URL for back-button support
  const setPhaseAndSave = useCallback((newPhase: ClonePhase) => {
    setPhase(newPhase);
    setHighestStep((prev) => {
      const PHASES: ClonePhase[] = ["source", "audience", "preview"];
      return PHASES.indexOf(newPhase) > PHASES.indexOf(prev) ? newPhase : prev;
    });
    if (avatarId) {
      avatarApi.updateAvatar(avatarId, { wizard_step: newPhase }).catch(() => { });
      navigate(`/my-avatar/clone/${avatarId}/${newPhase}`, { replace: false });
    }
  }, [avatarId, navigate]);

  const ensureAvatarId = useCallback(async () => {
    if (avatarId) return avatarId;
    if (createPromiseRef.current) return createPromiseRef.current;

    const createPromise = avatarApi.cloneCreate({})
      .then((res) => {
        setAvatarId(res.avatar_id);
        navigate(`/my-avatar/clone/${res.avatar_id}/${phase}`, { replace: true });
        return res.avatar_id;
      })
      .catch((err) => {
        setCreateError(true);
        toast({
          title: "Failed to initialize avatar",
          description: err?.response?.data?.detail || "Please try again",
          variant: "destructive",
        });
        throw err;
      })
      .finally(() => {
        createPromiseRef.current = null;
      });

    createPromiseRef.current = createPromise;
    return createPromise;
  }, [avatarId, navigate, phase]);

  // Handle resume from URL param (deep-link or query param)
  useEffect(() => {
    if (resumeHandled.current) return;
    const resumeId = resumeAvatarId || searchParams.get("resume");
    if (!resumeId) return;
    resumeHandled.current = true;
    setAvatarId(resumeId);

    avatarApi.status(resumeId).then((avatar: any) => {
      const validSteps: ClonePhase[] = ["source", "audience", "preview"];
      const savedStep = avatar.wizard_step as ClonePhase | undefined;

      // Map old step names to new ones for backward compatibility
      const stepMap: Record<string, ClonePhase> = {
        upload: "source",
        select: "source",
        setup: "audience",
        preview: "preview",
        source: "source",
        audience: "audience",
      };

      const mappedSaved = savedStep ? (stepMap[savedStep] || "source") : null;
      const entityStep = mappedSaved && validSteps.includes(mappedSaved) ? mappedSaved : null;

      // Reconstruct sourceData from server fields so audience/preview phases can render on resume
      if (avatar.name || avatar.face_ref_key) {
        const candidates: FaceCandidate[] = (avatar.candidate_frames || []).map((url: string) => ({
          url,
          r2_key: url.replace("https://media.luminacast.com/", ""),
          score: 1,
        }));
        setSourceData({
          name: avatar.name || "",
          gender: avatar.gender || "",
          voicePreviewText: avatar.test_script || DEFAULT_PREVIEW_TEXT,
          selectedFaceIdx: findSelectedFaceIdx(candidates, avatar.face_ref_key),
          candidates,
          layout: avatar.layout || "9:16",
        });
      }

      const hydratedAudience = parseAudienceData((avatar.target_audience as Record<string, unknown> | undefined) || null);
      if (hydratedAudience) {
        setAudienceData(hydratedAudience);
      }

      // Set highestStep based on saved step
      if (entityStep) {
        setHighestStep(entityStep);
      }

      let targetPhase: ClonePhase = "source";
      if (resumeStep) {
        const mappedResume = stepMap[resumeStep] || resumeStep;
        if (validSteps.includes(mappedResume as ClonePhase)) {
          const requestedStep = mappedResume as ClonePhase;
          if (entityStep) {
            const entityIdx = validSteps.indexOf(entityStep);
            const requestedIdx = validSteps.indexOf(requestedStep);
            targetPhase = requestedIdx <= entityIdx ? requestedStep : entityStep;
          } else {
            targetPhase = requestedStep;
          }
          setPhase(targetPhase);
          return;
        }
      }

      if (entityStep) {
        setPhase(entityStep);
        return;
      }

      // Fallback to status-based resume
      if (avatar.status === "READY" || avatar.status === "APPROVED" || avatar.status === "PROCESSING") {
        if (avatar.face_ref_key) {
          setPhase("preview");
          setHighestStep("preview");
        } else {
          setPhase("source");
        }
      } else if (avatar.status === "FACE_CANDIDATES_READY" || avatar.status === "CANDIDATES_READY") {
        setPhase("source");
      } else {
        setPhase("source");
      }
    }).catch(() => {
      setAvatarId(null);
      setPhase("source");
    });
  }, [searchParams, resumeAvatarId, resumeStep]);

  // Track the highest step reached for forward navigation
  const [highestStep, setHighestStep] = useState<ClonePhase>("source");
  const [visitedSteps, setVisitedSteps] = useState<Set<ClonePhase>>(new Set(["source"]));

  const PHASE_ORDER: ClonePhase[] = ["source", "audience", "preview"];

  const completedSteps = PHASE_ORDER.slice(0, PHASE_ORDER.indexOf(highestStep) + 1);


  // Forward button: show if user navigated back and a higher step was previously reached
  const canGoForward = PHASE_ORDER.indexOf(highestStep) > PHASE_ORDER.indexOf(phase);
  const handleForward = useCallback(() => {
    const currentIdx = PHASE_ORDER.indexOf(phase);
    if (currentIdx < PHASE_ORDER.indexOf(highestStep)) {
      const nextPhase = PHASE_ORDER[currentIdx + 1];
      setPhaseAndSave(nextPhase);
    }
  }, [phase, highestStep, setPhaseAndSave]);

  const handleStepClick = useCallback((key: string) => {
    const targetPhase = key as ClonePhase;
    // Only allow navigating to completed steps
    if (completedSteps.includes(targetPhase)) {
      setPhaseAndSave(targetPhase);
    }
  }, [completedSteps, setPhaseAndSave]);

  const handleBack = useCallback(() => {
    const phases: ClonePhase[] = ["source", "audience", "preview"];
    const currentIdx = phases.indexOf(phase);
    if (currentIdx > 0) {
      const prevPhase = phases[currentIdx - 1];
      setPhaseAndSave(prevPhase);
    }
  }, [phase, setPhaseAndSave]);

  if (createError) {
    return (
      <div className="text-center py-8">
        <p className="text-sm text-red-400">Failed to initialize avatar. Please refresh and try again.</p>
      </div>
    );
  }

  if (isResumeFlow && !avatarId) {
    return (
      <div className="flex flex-col items-center gap-3 py-8">
        <Loader2 className="h-8 w-8 text-accent animate-spin" />
        <p className="text-sm text-text-muted">Initializing...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="clone-flow">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-text">Clone Your Avatar</h2>
      </div>

      <StepIndicator
        steps={CLONE_STEPS}
        currentStep={phase}
        completedSteps={completedSteps}
        onStepClick={handleStepClick}
      />

      <div className={phase === "source" ? "block" : "hidden"}>
        <CloneSourcePhase
          avatarId={avatarId}
          ensureAvatarId={ensureAvatarId}
          initialData={sourceData}
          onComplete={(data) => {
            setSourceData(data);
            setVisitedSteps((prev) => new Set([...prev, "source"]));
            setPhaseAndSave("audience");
          }}
          onLiveChange={setSourceData}
        />
        {canGoForward && (
          <div className="flex justify-end">
            <Button variant="outline" size="sm" className="gap-1.5" onClick={handleForward}>
              Forward <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>

      {sourceData && avatarId && (
        <div className={phase === "audience" ? "block" : "hidden"}>
          <CloneAudiencePhase
            avatarId={avatarId}
            sourceData={sourceData}
            initialData={audienceData}
            onBack={handleBack}
            onComplete={(data) => {
              setAudienceData(data);
              setVisitedSteps((prev) => new Set([...prev, "audience"]));
              setPhaseAndSave("preview");
            }}
          />
          {canGoForward && (
            <div className="flex justify-end">
              <Button variant="outline" size="sm" className="gap-1.5" onClick={handleForward}>
                Forward <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </div>
      )}

      {phase === "preview" && sourceData && avatarId && (
        <ClonePreviewPhase
          avatarId={avatarId}
          sourceData={sourceData}
          onBack={handleBack}
        />
      )}

      {/* Fallback if we hit audience/preview without sourceData (e.g. resume) — fetch from server */}
      {phase !== "source" && !sourceData && (
        <div className="text-center py-8 space-y-3">
          <Loader2 className="h-6 w-6 text-accent animate-spin mx-auto" />
          <p className="text-sm text-text-muted">Loading session data...</p>
        </div>
      )}
    </div>
  );
}
