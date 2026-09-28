import { useState, useRef, useEffect } from "react";
import { useNavigate, useLocation, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  User,
  Loader2,
  CheckCircle,
  Camera,
  Wand2,
  Trash2,
  RefreshCw,
  Sparkles,
  ArrowRight,
  Link,
  Upload,
  Video as VideoIcon,
  Play,
  Maximize2,
  X,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { avatarApi, queryClient } from "@/lib/api";
import { AvatarStatus, AvatarType, type Avatar } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import { cdnUrl } from "@/lib/cdn";
import { CloneFlow } from "@/components/avatar/CloneFlow";
import { confirmAction } from "@/lib/swal";
import { playerAspectRatio } from "@/lib/layoutOptions";


export function SetupPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { avatarId: routeAvatarId, step: routeStep } = useParams<{ avatarId?: string; step?: string }>();
  const isCloneRoute = location.pathname.startsWith("/my-avatar/clone");

  // View modes
  const [showCloneFlow, setShowCloneFlow] = useState(isCloneRoute);

  // Sync view mode with URL
  useEffect(() => {
    if (location.pathname === "/my-avatar") {
      setShowCloneFlow(false);
    } else if (location.pathname.startsWith("/my-avatar/clone")) {
      setShowCloneFlow(true);
    }
  }, [location.pathname]);

  // Avatar list query
  const { data: avatarData, isLoading: avatarsLoading } = useQuery({
    queryKey: ["avatars"],
    queryFn: () => avatarApi.list(),
    refetchInterval: 5000,
  });
  const avatars: Avatar[] = (avatarData as any)?.avatars ?? [];

  // Both creation flows hit the same plan-level avatar slot limit on the
  // backend — check it here so an at-capacity user sees that up front
  // instead of clicking through to a creation flow that's just going to
  // 402 on them.
  const { data: slotSummary } = useQuery({
    queryKey: ["avatar-slots"],
    queryFn: () => avatarApi.getSlotSummary(),
  });
  const atSlotLimit = !!slotSummary && slotSummary.remaining <= 0;

  // Shared state for exclusive video playback
  const [activeVideoId, setActiveVideoId] = useState<string | null>(null);

  // Clone flow
  if (showCloneFlow) {
    return (
      <div className="space-y-6" data-testid="setup-page">
        <Card>
          <CardContent className="p-6">
            <CloneFlow resumeAvatarId={routeAvatarId} resumeStep={routeStep} />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="setup-page">
      <div>
        <h1 className="text-2xl font-bold text-text">My Avatar</h1>
        <p className="text-sm text-text-dim">Create and manage your digital clones and AI characters</p>
      </div>

      {/* ── Create New Avatar Section ── */}
      <div className="grid gap-4 md:grid-cols-2">
        <button
          onClick={() => {
            if (atSlotLimit) {
              toast({
                title: "Avatar limit reached",
                description: `You've used all ${slotSummary?.total} avatar slots on your plan. Upgrade your plan or purchase an additional avatar slot to continue.`,
                variant: "destructive",
              });
              return;
            }
            setShowCloneFlow(true);
            navigate("/my-avatar/clone");
          }}
          disabled={atSlotLimit}
          className={cn(
            "group flex items-start gap-4 rounded-xl border-2 border-dashed border-border bg-surface p-6 text-left transition-all",
            atSlotLimit ? "opacity-50 cursor-not-allowed" : "hover:border-accent hover:bg-accent/5",
          )}
          data-testid="create-clone-card"
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/10 group-hover:bg-accent/20 transition-colors">
            <Camera className="h-6 w-6 text-accent" />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-semibold text-text">Clone yourself</h3>
            <p className="mt-1 text-xs text-text-dim">From social media, upload video, or record live</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="inline-flex items-center gap-1 rounded bg-surface/80 px-2 py-0.5 text-[10px] text-text-muted border border-border">
                <Link className="h-2.5 w-2.5" /> Social media
              </span>
              <span className="inline-flex items-center gap-1 rounded bg-surface/80 px-2 py-0.5 text-[10px] text-text-muted border border-border">
                <Upload className="h-2.5 w-2.5" /> Upload video
              </span>
              <span className="inline-flex items-center gap-1 rounded bg-surface/80 px-2 py-0.5 text-[10px] text-text-muted border border-border">
                <VideoIcon className="h-2.5 w-2.5" /> Record now
              </span>
            </div>
            {atSlotLimit ? (
              <p className="mt-2 text-xs text-red-400 font-medium">Limit reached — upgrade to add more</p>
            ) : (
              <div className="mt-2 flex items-center text-xs text-accent font-medium">
                Get started <ArrowRight className="ml-1 h-3 w-3" />
              </div>
            )}
          </div>
        </button>

        <button
          onClick={() => {
            if (atSlotLimit) {
              toast({
                title: "Avatar limit reached",
                description: `You've used all ${slotSummary?.total} avatar slots on your plan. Upgrade your plan or purchase an additional avatar slot to continue.`,
                variant: "destructive",
              });
              return;
            }
            navigate("/my-avatar/ai-avatar");
          }}
          disabled={atSlotLimit}
          className={cn(
            "group flex items-start gap-4 rounded-xl border-2 border-dashed border-border bg-surface p-6 text-left transition-all",
            atSlotLimit ? "opacity-50 cursor-not-allowed" : "hover:border-purple-500 hover:bg-purple-500/5",
          )}
          data-testid="create-digital-card"
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-purple-100 group-hover:bg-purple-200 transition-colors">
            <Wand2 className="h-6 w-6 text-purple-600" />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-semibold text-text">AI avatar</h3>
            <p className="mt-1 text-xs text-text-dim">Generate a digital character with custom face and voice</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="inline-flex items-center gap-1 rounded bg-surface/80 px-2 py-0.5 text-[10px] text-text-muted border border-border">
                Describe face
              </span>
              <span className="inline-flex items-center gap-1 rounded bg-surface/80 px-2 py-0.5 text-[10px] text-text-muted border border-border">
                Pick voice
              </span>
              <span className="inline-flex items-center gap-1 rounded bg-surface/80 px-2 py-0.5 text-[10px] text-text-muted border border-border">
                Preview & approve
              </span>
            </div>
            {atSlotLimit ? (
              <p className="mt-2 text-xs text-red-400 font-medium">Limit reached — upgrade to add more</p>
            ) : (
              <div className="mt-2 flex items-center text-xs text-purple-600 font-medium">
                Create character <ArrowRight className="ml-1 h-3 w-3" />
              </div>
            )}
          </div>
        </button>
      </div>

      {/* ── Your Avatars ── */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">Your Avatars</CardTitle>
              <CardDescription>Manage your digital clones and AI characters</CardDescription>
            </div>
            <Badge variant="secondary">{avatars.length} avatar{avatars.length !== 1 ? "s" : ""}</Badge>
          </div>
        </CardHeader>
        <CardContent>
          {avatarsLoading ? (
            <div className="flex items-center gap-2 py-6 justify-center">
              <Loader2 className="h-4 w-4 animate-spin text-accent" />
              <span className="text-sm text-text-muted">Loading avatars...</span>
            </div>
          ) : avatars.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border py-8 text-center">
              <User className="mx-auto mb-2 h-8 w-8 text-text-muted" />
              <p className="text-sm text-text-muted">No avatars yet. Create one above.</p>
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {avatars.map((avatar) => (
                <AvatarCard key={avatar.id} avatar={avatar} activeVideoId={activeVideoId} setActiveVideoId={setActiveVideoId} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}


// ── Avatar Card Component ──

const PORTRAIT_THUMBNAIL_WIDTH = 200;

function AvatarCard({ avatar, activeVideoId, setActiveVideoId }: { avatar: Avatar; activeVideoId: string | null; setActiveVideoId: (id: string | null) => void }) {
  const navigate = useNavigate();
  const [fullscreenVideo, setFullscreenVideo] = useState<string | null>(null);
  const isVideoPlaying = activeVideoId === avatar.id;
  const videoRef = useRef<HTMLVideoElement>(null);

  // Pause this video when another one becomes active
  useEffect(() => {
    if (activeVideoId && activeVideoId !== avatar.id && videoRef.current) {
      videoRef.current.pause();
      videoRef.current.muted = true;
    }
  }, [activeVideoId, avatar.id]);
  const isDraft = avatar.status === AvatarStatus.DRAFT;
  const isProcessing = avatar.status === AvatarStatus.PROCESSING;
  const isFaceCandidatesReady = avatar.status === AvatarStatus.FACE_CANDIDATES_READY;
  const isCandidatesReady = avatar.status === AvatarStatus.CANDIDATES_READY;
  const isReady = avatar.status === AvatarStatus.READY;
  const isApproved = avatar.status === AvatarStatus.APPROVED;
  const isFailed = avatar.status === AvatarStatus.FAILED;
  // isProcessing deliberately excluded: the wizard this navigates into
  // (AIAvatarSetup.tsx) infers which step to resume on purely from which
  // output fields already exist (preview_video_url, voice_id, etc.) — it
  // has no awareness of "a render is actively in flight right now". While
  // that render's own output field is still empty, resuming lands on an
  // earlier, fully-editable step where Generate can be clicked again,
  // launching a second overlapping job for the same avatar. Simplest safe
  // fix: don't make the card clickable at all while it's mid-render — the
  // amber "Processing..." badge (progress_step) already communicates that
  // state, this just stops it from being an entry point back into the form.
  //
  // EXCEPTION: an AI avatar that was CREATED but has generated nothing yet.
  // create_ai_avatar historically stamped status=PROCESSING before any
  // pipeline ran, so leaving the wizard mid-setup left the card stuck and
  // un-resumable. Nothing is in flight (no candidates, no face, no voice, no
  // preview, 0%), so resuming into the wizard is safe — it just lands on
  // step 1.
  const isDigital = avatar.type === AvatarType.DIGITAL;
  const isAiSetupNotStarted =
    isDigital &&
    isProcessing &&
    (avatar.progress_percent ?? 0) === 0 &&
    !avatar.face_ref_key &&
    !(avatar.candidate_frames && avatar.candidate_frames.length > 0) &&
    !avatar.voice_id &&
    !avatar.preview_video_url;
  const isClickableForResume =
    isDraft || isFaceCandidatesReady || isCandidatesReady || isFailed || isAiSetupNotStarted;
  const [showRegenInput, setShowRegenInput] = useState(false);
  const [isRecloning, setIsRecloning] = useState(false);
  const defaultScript = "Hi everyone! Welcome to my stream. I'm so excited to show you some amazing products today!";
  const regenInputRef = useRef<HTMLTextAreaElement>(null);
  const [charCount, setCharCount] = useState((avatar.test_script || defaultScript).length);
  const FREE_REGENERATIONS = 2;
  const regenRemaining = FREE_REGENERATIONS - (avatar.regeneration_count || 0);
  const regenExhausted = regenRemaining <= 0;

  const statusBadge = () => {
    if (isDraft) return <Badge className="bg-gray-900/50 text-gray-400 border-gray-700">Draft</Badge>;
    if (isApproved) return <Badge className="bg-green-900/50 text-green-400 border-green-700">Approved</Badge>;
    if (isAiSetupNotStarted) return <Badge className="bg-blue-900/50 text-blue-400 border-blue-700">Continue setup &rarr;</Badge>;
    if (isProcessing) return <Badge className="bg-amber-900/50 text-amber-400 border-amber-700">{avatar.progress_step || "Processing..."}</Badge>;
    if (isFaceCandidatesReady) return <Badge className="bg-blue-900/50 text-blue-400 border-blue-700">Tap to pick your face &rarr;</Badge>;
    if (isCandidatesReady) return <Badge className="bg-blue-900/50 text-blue-400 border-blue-700">Awaiting selection</Badge>;
    if (isFailed) return <Badge className="bg-red-900/50 text-red-400 border-red-700">Failed</Badge>;
    if (isReady) return <Badge className="bg-purple-900/50 text-purple-400 border-purple-700">Ready for review</Badge>;
    return null;
  };

  const faceRefSource = avatar.face_ref_key
    || (avatar.candidate_frames && avatar.candidate_frames.length > 0 ? avatar.candidate_frames[0] : null);
  const faceImageUrl = faceRefSource
    ? cdnUrl(faceRefSource)
    : null;

  const handleDelete = async () => {
    const confirmed = await confirmAction({
      title: `Delete avatar "${avatar.name || "Untitled"}"?`,
      text: "This action cannot be undone.",
      confirmButtonText: "Delete",
    });
    if (!confirmed) return;

    avatarApi.delete(avatar.id).then(() => {
      queryClient.invalidateQueries({ queryKey: ["avatars"] });
      toast({ title: "Avatar deleted", variant: "default" });
    }).catch(() => {
      toast({ title: "Failed to delete avatar", variant: "destructive" });
    });
  };

  return (
    <div
      className={cn(
        "relative rounded-lg border p-4 transition-all hover:border-purple-500",
        isDraft && "border-gray-600 bg-gray-900/20 border-dashed",
        isApproved && "border-green-700 bg-green-900/20",
        isProcessing && !isAiSetupNotStarted && "border-amber-700 bg-amber-900/20",
        (isFaceCandidatesReady || isCandidatesReady || isAiSetupNotStarted) && "border-blue-700 bg-blue-900/20",
        isFailed && "border-red-700 bg-red-900/20",
        isReady && "border-purple-500 bg-purple-900/20",
        !isDraft && !isProcessing && !isCandidatesReady && !isFaceCandidatesReady && !isReady && !isFailed && !isApproved && "border-gray-700 bg-surface",
        (isClickableForResume || isApproved || isReady) && "cursor-pointer",
      )}
      onClick={isClickableForResume ? () => navigate(avatar.type === AvatarType.DIGITAL ? `/my-avatar/ai/${avatar.id}` : `/my-avatar/clone/${avatar.id}`) : (isApproved || isReady) ? () => navigate(`/my-avatar/${avatar.id}/edit`) : undefined}
      data-testid={`avatar-card-${avatar.id}`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          {faceImageUrl ? (
            <img src={faceImageUrl} alt={avatar.name || ""} className="h-10 w-10 rounded-full object-cover border border-border" />
          ) : (
            <div className={cn(
              "flex h-10 w-10 items-center justify-center rounded-full",
              avatar.type === AvatarType.CLONE ? "bg-accent/20" : "bg-purple-100",
            )}>
              {avatar.type === AvatarType.CLONE ? (
                <Camera className="h-5 w-5 text-accent" />
              ) : (
                <Wand2 className="h-5 w-5 text-purple-600" />
              )}
            </div>
          )}
          <div>
            <p className="text-sm font-medium text-text">{avatar.name || "Untitled Avatar"}</p>
            <p className="text-[10px] text-text-muted">
              {avatar.type === AvatarType.CLONE ? "Clone" : "AI Avatar"}
              {avatar.created_at && ` · ${new Date(avatar.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`}
            </p>
          </div>
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); handleDelete(); }}
          className="rounded p-1 text-text-muted hover:bg-red-100 hover:text-red-600 transition-colors"
          title="Delete avatar"
          data-testid={`delete-avatar-${avatar.id}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3">{statusBadge()}</div>

      {(isDraft || isFaceCandidatesReady || isCandidatesReady || isAiSetupNotStarted) && (
        <button
          onClick={(e) => { e.stopPropagation(); navigate(avatar.type === AvatarType.DIGITAL ? `/my-avatar/ai/${avatar.id}` : `/my-avatar/clone/${avatar.id}`); }}
          className="mt-2 text-xs text-accent hover:underline flex items-center gap-1 font-medium"
        >
          Continue <ArrowRight className="h-3 w-3" />
        </button>
      )}

      {isApproved && (
        <button
          onClick={(e) => { e.stopPropagation(); navigate(`/my-avatar/${avatar.id}/edit`); }}
          className="mt-2 text-xs text-accent hover:underline flex items-center gap-1"
        >
          Edit profile <ArrowRight className="h-3 w-3" />
        </button>
      )}


      {isProcessing && !isAiSetupNotStarted && (
        <div className="mt-2 space-y-1">
          <Progress value={avatar.progress_percent || 0} className="h-1.5" />
          <p className="text-[10px] text-text-muted">{Math.round(avatar.progress_percent || 0)}% complete</p>
        </div>
      )}

      {(isReady || isApproved) && (avatar.preview_video_url || avatar.test_video_url || avatar.face_image_url) && (
        <div className="mt-3">
          {/* Face thumbnail with click-to-play video.
              Portrait boxes are sized by width (200px wide, tall box). A
              landscape (16:9) box sized the same way ends up very short —
              correct, but reads as "tiny" next to a portrait card in the
              same grid row. Size it by height instead, matching the height
              a portrait box renders at, and let width fill the card
              (object-cover on the media crops the sides as needed). */}
          <div
            className="group relative mx-auto cursor-pointer rounded-lg overflow-hidden border border-border"
            style={
              avatar.layout === "16:9"
                ? { height: PORTRAIT_THUMBNAIL_WIDTH * (16 / 9), width: "100%" }
                : { maxWidth: PORTRAIT_THUMBNAIL_WIDTH, aspectRatio: playerAspectRatio(avatar.layout) }
            }
            onClick={(e) => {
              e.stopPropagation();
              const videoUrl = avatar.preview_video_url || avatar.test_video_url;
              if (!videoUrl || !videoRef.current) return;
              if (isVideoPlaying) {
                videoRef.current.pause();
                videoRef.current.muted = true;
                setActiveVideoId(null);
              } else {
                videoRef.current.muted = false;
                videoRef.current.play();
                setActiveVideoId(avatar.id);
              }
            }}
          >
            {/* Static face image — hidden when video is playing */}
            {avatar.face_image_url && (
              <img
                src={avatar.face_image_url}
                alt={avatar.name}
                className={cn(
                  "absolute inset-0 w-full h-full object-cover transition-opacity duration-300",
                  isVideoPlaying ? "opacity-0" : "group-hover:opacity-0",
                )}
              />
            )}
            {/* Video — plays on hover or click; prefers preview_video_url */}
            {(avatar.preview_video_url || avatar.test_video_url) && (
              <video
                ref={videoRef}
                src={avatar.preview_video_url || avatar.test_video_url}
                playsInline preload="metadata" muted loop
                className="w-full h-full object-cover"
                onMouseEnter={(e) => { if (!isVideoPlaying) (e.target as HTMLVideoElement).play(); }}
                onMouseLeave={(e) => { if (!isVideoPlaying) { const v = e.target as HTMLVideoElement; v.pause(); v.currentTime = 0; } }}
                data-testid={`avatar-video-${avatar.id}`}
              />
            )}
            {!avatar.face_image_url && !avatar.test_video_url && (
              <div className="w-full h-full bg-border/30" />
            )}
            {/* Play/pause overlay */}
            {!isVideoPlaying && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/30 transition-all">
                <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                  <Play className="h-10 w-10 text-white drop-shadow-lg" />
                </div>
              </div>
            )}
            {/* Fullscreen button */}
            {(avatar.preview_video_url || avatar.test_video_url) && (
              <button
                className="absolute bottom-2 right-2 rounded-full bg-black/50 p-1.5 text-white/70 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity z-10"
                onClick={(e) => {
                  e.stopPropagation();
                  // Pause the inline video when opening fullscreen
                  if (videoRef.current) {
                    videoRef.current.pause();
                    videoRef.current.muted = true;
                  }
                  setActiveVideoId(null);
                  setFullscreenVideo(avatar.preview_video_url || avatar.test_video_url!);
                }}
                title="Fullscreen"
              >
                <Maximize2 className="h-4 w-4" />
              </button>
            )}
          </div>
          <p className="mt-1.5 text-[10px] text-text-muted italic line-clamp-2 text-center">
            "{avatar.test_script || defaultScript}"
          </p>

          {isReady && (
            <div className="mt-2 space-y-2">
              <div className="flex gap-2">
                <Button
                  size="sm" variant="outline"
                  className="flex-1 text-[10px] text-green-600 border-green-200 hover:bg-green-50"
                  onClick={(e) => {
                    e.stopPropagation();
                    avatarApi.approve(avatar.id).then(() => {
                      queryClient.invalidateQueries({ queryKey: ["avatars"] });
                      toast({ title: "Avatar approved", description: "Ready for Cast creation.", variant: "success" });
                    }).catch(() => toast({ title: "Approval failed", variant: "destructive" }));
                  }}
                  data-testid={`approve-avatar-${avatar.id}`}
                >
                  <CheckCircle className="mr-1 h-3 w-3" /> Approve
                </Button>
                <Button
                  size="sm" variant="outline"
                  className="flex-1 text-[10px]"
                  disabled={regenExhausted}
                  onClick={(e) => { e.stopPropagation(); setShowRegenInput(!showRegenInput); }}
                  data-testid={`regenerate-avatar-${avatar.id}`}
                >
                  <RefreshCw className="mr-1 h-3 w-3" /> {regenExhausted ? "Limit reached" : "Regenerate"}
                </Button>
              </div>

              {showRegenInput && (
                <div className="space-y-1.5">
                  <textarea
                    ref={regenInputRef}
                    defaultValue={avatar.test_script || defaultScript}
                    onInput={(e) => setCharCount((e.target as HTMLTextAreaElement).value.length)}
                    placeholder="What should the avatar say? (max 200 chars)"
                    className="w-full rounded-md border border-border bg-surface p-2 text-[10px] text-text resize-none focus:border-accent focus:outline-hidden"
                    rows={2} maxLength={200}
                    data-testid={`regen-script-${avatar.id}`}
                  />
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-text-muted">{charCount}/200</span>
                    {regenExhausted ? (
                      <span className="text-[10px] text-text-muted">Additional: $1.99 (coming soon)</span>
                    ) : (
                      <Button
                        size="sm" className="text-[10px] h-6 px-2"
                        onClick={() => {
                          const script = regenInputRef.current?.value || avatar.test_script || defaultScript;
                          avatarApi.regenerate(avatar.id, script).then(() => {
                            queryClient.invalidateQueries({ queryKey: ["avatars"] });
                            toast({ title: "Regenerating...", description: "~2 min for new video.", variant: "default" });
                            setShowRegenInput(false);
                          }).catch((err: any) => toast({ title: "Regeneration failed", description: err?.response?.data?.detail || undefined, variant: "destructive" }));
                        }}
                      >
                        <Sparkles className="mr-1 h-3 w-3" /> Generate
                      </Button>
                    )}
                  </div>
                  <p className="text-[10px] text-text-muted">
                    {regenExhausted ? `0/${FREE_REGENERATIONS} free remaining` : `${regenRemaining}/${FREE_REGENERATIONS} free remaining`}
                  </p>
                </div>
              )}

              {avatar.type === AvatarType.CLONE && (
                <Button
                  size="sm" variant="outline"
                  className="w-full text-[10px] text-purple-300 border-purple-500/30 hover:bg-purple-500/10"
                  disabled={isRecloning}
                  onClick={() => {
                    setIsRecloning(true);
                    avatarApi.recloneVoice(avatar.id).then(() => {
                      queryClient.invalidateQueries({ queryKey: ["avatars"] });
                      toast({ title: "Voice re-clone started", description: "Re-running voice pipeline. This takes ~2 min.", variant: "default" });
                    }).catch((err: any) => {
                      toast({ title: "Re-clone failed", description: err?.response?.data?.detail || "Try again later.", variant: "destructive" });
                    }).finally(() => setIsRecloning(false));
                  }}
                  data-testid={`reclone-voice-${avatar.id}`}
                >
                  {isRecloning ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />}
                  {isRecloning ? "Re-cloning voice..." : "Re-clone Voice"}
                </Button>
              )}
            </div>
          )}
        </div>
      )}


      {(isReady || isApproved) && !avatar.test_video_url && (
        <div className="mt-2 flex flex-wrap gap-1">
          {avatar.voice_style && <Badge variant="secondary" className="text-[10px]">Voice: {avatar.voice_style}</Badge>}
          {avatar.style && <Badge variant="secondary" className="text-[10px]">Style: {avatar.style}</Badge>}
        </div>
      )}

      {/* Fullscreen video modal */}
      {fullscreenVideo && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
          onClick={() => setFullscreenVideo(null)}
        >
          <div className="relative max-w-sm w-full mx-4" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <video
              src={fullscreenVideo}
              controls autoPlay playsInline
              className="w-full rounded-xl"
              style={{ aspectRatio: playerAspectRatio(avatar.layout) }}
            />
            <button
              onClick={() => setFullscreenVideo(null)}
              className="absolute top-2 right-2 rounded-full bg-black/50 p-1.5 text-white/70 hover:text-white transition"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
