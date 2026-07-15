import { useState, useCallback, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Camera, Wand2, Trash2, RefreshCw, Sparkles, Star, Plus, Loader2,
  Play, X, Calendar, Mic, RotateCcw,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { avatarApi, avatarLooksApi, queryClient } from "@/lib/api";
import { AvatarStatus, AvatarType, type Avatar, type AvatarLook } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { cdnUrl } from "@/lib/cdn";
import { AddLookDialog } from "@/components/avatar/AddLookDialog";
import { VoiceCorpusTab } from "@/components/avatar/VoiceCorpusTab";
import { StyleDNA } from "@/components/avatar/StyleDNA";
import { LiveReferenceCard } from "@/components/avatar/LiveReferenceCard";
import { confirmAction } from "@/lib/swal";
// AvatarIdentityPanel removed — edit page uses single profile card instead

type LookTab = "background" | "body_motion" | "tryon" | "voice";

const POSE_LABELS: Record<string, string> = {
  front: "Front",
  three_quarter_left: "3/4 Left",
  three_quarter_right: "3/4 Right",
  profile_left: "Profile Left",
  profile_right: "Profile Right",
  back: "Back",
};

const ALL_POSES = [
  { value: "front", label: "Front" },
  { value: "three_quarter_left", label: "3/4 Left" },
  { value: "three_quarter_right", label: "3/4 Right" },
  { value: "profile_left", label: "Profile Left" },
  { value: "profile_right", label: "Profile Right" },
  { value: "back", label: "Back" },
];

export function EditAvatarPage() {
  const { avatarId } = useParams<{ avatarId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [fullscreenVideo, setFullscreenVideo] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<LookTab>("background");
  const [addOpen, setAddOpen] = useState(false);
  const [previewLook, setPreviewLook] = useState<AvatarLook | null>(null);
  const [nameValue, setNameValue] = useState("");
  const [descValue, setDescValue] = useState("");
  const [editingDesc, setEditingDesc] = useState(false);
  const [bodyDescValue, setBodyDescValue] = useState("");
  const nameInputRef = useRef<HTMLInputElement>(null);
  const mainVideoRef = useRef<HTMLVideoElement>(null);

  const FREE_REGENERATIONS = 2;
  const defaultScript = "Hi everyone! Welcome to my stream. I'm so excited to show you some amazing products today!";

  // Fetch avatar
  const { data: avatar, isLoading: avatarLoading } = useQuery({
    queryKey: ["avatar-status", avatarId],
    queryFn: () => avatarApi.status(avatarId!),
    enabled: !!avatarId,
    refetchInterval: (q) => {
      const a = q.state.data as Avatar | undefined;
      return a?.status === AvatarStatus.PROCESSING ? 3000 : false;
    },
  });

  // Fetch looks
  const { data: looksData, isLoading: looksLoading } = useQuery({
    queryKey: ["avatar-looks", avatarId],
    queryFn: () => avatarLooksApi.list(avatarId!),
    enabled: !!avatarId,
    refetchInterval: (q) => {
      const looks: AvatarLook[] = q.state.data?.looks || [];
      return looks.some((l) => l.status === "pending" || l.status === "generating") ? 3000 : false;
    },
  });

  const allLooks: AvatarLook[] = looksData?.looks || [];
  const filteredLooks = allLooks.filter((l) => (l.look_type || "background") === activeTab);

  useEffect(() => {
    if (avatar?.name) setNameValue(avatar.name);
    if (avatar?.description !== undefined) setDescValue(avatar.description || "");
    if (avatar?.body_description !== undefined) setBodyDescValue(avatar.body_description || "");
  }, [avatar?.name, avatar?.description, avatar?.body_description]);

  // Pause main video when fullscreen or look preview opens (A6 fix)
  useEffect(() => {
    if ((fullscreenVideo || previewLook) && mainVideoRef.current) {
      mainVideoRef.current.pause();
    }
  }, [fullscreenVideo, previewLook]);

  // Mutations
  const setDefaultMutation = useMutation({
    mutationFn: (lookId: string) => avatarLooksApi.setDefault(avatarId!, lookId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] }),
  });

  const deleteLookMutation = useMutation({
    mutationFn: (lookId: string) => avatarLooksApi.delete(avatarId!, lookId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] });
      toast({ title: "Look deleted" });
    },
    onError: (err: any) =>
      toast({ title: "Delete failed", description: err?.response?.data?.detail || "Unknown error", variant: "destructive" }),
  });

  const handleNameSave = useCallback(async () => {
    if (!avatar || nameValue === avatar.name) return;
    try {
      await avatarApi.update(avatarId!, { name: nameValue });
      qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
      qc.invalidateQueries({ queryKey: ["avatars"] });
      toast({ title: "Name updated" });
    } catch {
      toast({ title: "Failed to update name", variant: "destructive" });
    }
  }, [avatar, avatarId, nameValue, qc]);

  const handleDescSave = useCallback(async () => {
    if (!avatar || descValue === (avatar.description || "")) {
      setEditingDesc(false);
      return;
    }
    try {
      await avatarApi.updateAvatar(avatarId!, { description: descValue });
      qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
      toast({ title: "Description updated" });
    } catch {
      toast({ title: "Failed to update description", variant: "destructive" });
    }
    setEditingDesc(false);
  }, [avatar, avatarId, descValue, qc]);

  const handleBodyDescSave = useCallback(async () => {
    if (!avatar || bodyDescValue === (avatar.body_description || "")) return;
    try {
      await avatarApi.updateAvatar(avatarId!, { body_description: bodyDescValue });
      qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
      toast({ title: "Body description updated" });
    } catch {
      toast({ title: "Failed to update body description", variant: "destructive" });
    }
  }, [avatar, avatarId, bodyDescValue, qc]);

  const handleRegeneratePoses = useCallback(async () => {
    try {
      // Save body description first if changed.
      if (avatar && bodyDescValue !== (avatar.body_description || "")) {
        await avatarApi.updateAvatar(avatarId!, { body_description: bodyDescValue });
      }
      // generate-all-body-motion only fills in MISSING poses (it skips any
      // pose that already has a ready/pending/generating look). For an
      // explicit "Regenerate", we want to wipe the existing body_motion
      // looks first so the call actually re-rolls all 6. Delete in parallel
      // and ignore individual failures — the create call below will simply
      // skip whatever it finds.
      const existingBodyMotionLooks = allLooks.filter(
        (l) => l.look_type === "body_motion"
      );
      if (existingBodyMotionLooks.length) {
        await Promise.allSettled(
          existingBodyMotionLooks.map((l) => avatarLooksApi.delete(avatarId!, l.id))
        );
      }
      const result = await avatarLooksApi.generateAllBodyMotion(avatarId!);
      qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] });
      qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
      toast({
        title: `Regenerating ${result.created || 6} poses`,
        description: "This may take a few minutes.",
      });
    } catch (err: any) {
      toast({ title: "Failed to regenerate poses", description: err?.response?.data?.detail || err.message, variant: "destructive" });
    }
  }, [avatar, avatarId, bodyDescValue, allLooks, qc]);

  const handleDelete = useCallback(async () => {
    const confirmed = await confirmAction({
      title: `Delete avatar "${avatar?.name || "Untitled"}"?`,
      text: "This action cannot be undone.",
      confirmButtonText: "Delete",
    });
    if (!confirmed) return;

    avatarApi.delete(avatarId!).then(() => {
      qc.invalidateQueries({ queryKey: ["avatars"] });
      toast({ title: "Avatar deleted" });
      navigate("/my-avatar");
    }).catch(() => toast({ title: "Failed to delete avatar", variant: "destructive" }));
  }, [avatar, avatarId, navigate, qc]);

  if (avatarLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-accent" />
      </div>
    );
  }

  if (!avatar) {
    return (
      <div className="text-center py-20">
        <p className="text-text-muted">Avatar not found.</p>
        <Button variant="ghost" onClick={() => navigate("/my-avatar")} className="mt-4">
          <ArrowLeft className="mr-2 h-4 w-4" /> Back to My Avatar
        </Button>
      </div>
    );
  }

  const regenRemaining = FREE_REGENERATIONS - (avatar.regeneration_count || 0);
  const regenExhausted = regenRemaining <= 0;
  const faceImageUrl = avatar.face_ref_key ? cdnUrl(avatar.face_ref_key) : null;

  const tabs: { key: LookTab; label: string }[] = [
    { key: "background", label: "Scenes" },
    { key: "body_motion", label: "Body Motion" },
    { key: "tryon", label: "Try-On" },
    { key: "voice", label: "Voice Examples" },
  ];

  const emptyMessages: Record<LookTab, string> = {
    background: "No scenes yet. Add one to give your avatar new looks.",
    body_motion: "No body motion photos yet. Add angles to enable body motion casts in Session F.",
    tryon: "No try-on looks yet. Click 'Add Look' to generate your avatar wearing a product.",
    voice: "No voice examples yet. Upload videos of yourself talking.",
  };

  return (
    <div className="space-y-6" data-testid="edit-avatar-page">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate("/my-avatar")}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-xl font-bold text-text">Edit Avatar</h1>
      </div>

      <div className="grid gap-6 md:grid-cols-[320px_1fr]">
        {/* Left column — Profile Card */}
        <div className="space-y-4">
          <Card>
            <CardContent className="p-5 space-y-4">
              {/* Single avatar photo / video — click to play fullscreen.
                  Both branches share a `group` wrapper that owns the
                  overflow-hidden frame; the inner <img> scales on hover
                  for a soft zoom-in effect. A small in-image "Regenerate"
                  pill at the top-right is always faintly visible so the
                  user knows the avatar can be redrawn without scrolling
                  to the button below. */}
              {avatar.test_video_url ? (
                <div className="relative mx-auto group" style={{ maxWidth: 320 }}>
                  <button
                    className="relative block w-full overflow-hidden rounded-xl border border-border cursor-pointer"
                    onClick={() => setFullscreenVideo(avatar.test_video_url!)}
                  >
                    <img
                      src={faceImageUrl || avatar.test_video_url}
                      alt={avatar.name || ""}
                      className="w-full transition-transform duration-500 ease-out group-hover:scale-110"
                      style={{ aspectRatio: "9/16", objectFit: "cover" }}
                    />
                    <div className="absolute inset-0 flex items-center justify-center">
                      <div className="w-14 h-14 rounded-full bg-black/50 backdrop-blur-xs flex items-center justify-center group-hover:bg-accent/80 transition-colors">
                        <Play className="h-7 w-7 text-white ml-0.5" fill="white" />
                      </div>
                    </div>
                    <div className="absolute bottom-3 left-3 bg-black/60 text-white text-[11px] px-2 py-1 rounded-md backdrop-blur-xs">
                      Video Preview
                    </div>
                  </button>
                  <ProfileRegenerateOverlay
                    visible={!regenExhausted && (avatar.status === AvatarStatus.READY || avatar.status === AvatarStatus.APPROVED)}
                    onRegenerate={() => {
                      const script = avatar.test_script || defaultScript;
                      avatarApi.regenerate(avatarId!, script).then(() => {
                        qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
                        toast({ title: "Regenerating...", description: "~2 min for new video." });
                      }).catch(() => toast({ title: "Regeneration failed", variant: "destructive" }));
                    }}
                  />
                </div>
              ) : faceImageUrl ? (
                <div className="relative mx-auto group" style={{ maxWidth: 320 }}>
                  <div className="overflow-hidden rounded-xl border border-border">
                    <img
                      src={faceImageUrl}
                      alt={avatar.name || ""}
                      className="w-full transition-transform duration-500 ease-out group-hover:scale-110"
                      style={{ aspectRatio: "9/16", objectFit: "cover" }}
                    />
                  </div>
                  <ProfileRegenerateOverlay
                    visible={!regenExhausted && (avatar.status === AvatarStatus.READY || avatar.status === AvatarStatus.APPROVED)}
                    onRegenerate={() => {
                      const script = avatar.test_script || defaultScript;
                      avatarApi.regenerate(avatarId!, script).then(() => {
                        qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
                        toast({ title: "Regenerating...", description: "~2 min for new video." });
                      }).catch(() => toast({ title: "Regeneration failed", variant: "destructive" }));
                    }}
                  />
                </div>
              ) : (
                <div className="mx-auto flex items-center justify-center rounded-xl bg-accent/10 border border-border" style={{ maxWidth: 320, aspectRatio: "9/16" }}>
                  {avatar.type === AvatarType.CLONE ? <Camera className="h-12 w-12 text-accent" /> : <Wand2 className="h-12 w-12 text-purple-600" />}
                </div>
              )}

              {/* Name and type */}
              <div className="flex items-center gap-2">
                <input
                  ref={nameInputRef}
                  type="text"
                  value={nameValue}
                  onChange={(e) => setNameValue(e.target.value)}
                  onBlur={handleNameSave}
                  onKeyDown={(e) => e.key === "Enter" && handleNameSave()}
                  maxLength={200}
                  className="text-lg font-bold bg-transparent border-b border-transparent hover:border-border focus:border-accent focus:outline-hidden text-text w-full truncate"
                  placeholder="Avatar name"
                />
                <Badge variant="secondary" className="shrink-0">
                  {avatar.type === AvatarType.CLONE ? "Clone" : "AI Avatar"}
                </Badge>
                {avatar.detected_language && (
                  <Badge variant="outline" className="shrink-0 text-xs">
                    {avatar.detected_language.toUpperCase()}
                  </Badge>
                )}
              </div>

              {/* Voice indicator */}
              {avatar.voice_id && (
                <div className="flex items-center gap-2">
                  <Mic className="h-4 w-4 text-text-muted" />
                  <span className="text-xs text-text-muted">Voice clone active</span>
                </div>
              )}

              {/* Created date */}
              {avatar.created_at && (
                <div className="flex items-center gap-2 text-xs text-text-muted">
                  <Calendar className="h-3.5 w-3.5" />
                  Created {new Date(avatar.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                </div>
              )}

              {/* Description — click to edit */}
              <div className="mt-1">
                {editingDesc ? (
                  <textarea
                    autoFocus
                    value={descValue}
                    onChange={(e) => setDescValue(e.target.value)}
                    onBlur={handleDescSave}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleDescSave(); } }}
                    rows={3}
                    className="w-full bg-transparent text-sm text-white/70 border border-border rounded-md px-2 py-1.5 resize-none outline-none focus:border-accent"
                    placeholder="Add a description..."
                  />
                ) : (
                  <button
                    onClick={() => setEditingDesc(true)}
                    className="w-full text-left"
                  >
                    <p className="text-sm text-white/50 leading-relaxed hover:text-white/70 transition-colors">
                      {descValue || "No description \u2014 click to add one"}
                    </p>
                  </button>
                )}
              </div>

              {/* Regenerate */}
              {!regenExhausted && (avatar.status === AvatarStatus.READY || avatar.status === AvatarStatus.APPROVED) && (
                <Button
                  size="sm" variant="outline" className="w-full text-xs"
                  onClick={() => {
                    const script = avatar.test_script || defaultScript;
                    avatarApi.regenerate(avatarId!, script).then(() => {
                      qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] });
                      toast({ title: "Regenerating...", description: "~2 min for new video." });
                    }).catch(() => toast({ title: "Regeneration failed", variant: "destructive" }));
                  }}
                >
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Regenerate ({regenRemaining} free left)
                </Button>
              )}

              {/* Delete */}
              <Button
                size="sm" variant="outline" className="w-full text-xs text-red-500 border-red-200 hover:bg-red-50"
                onClick={handleDelete}
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Delete Avatar
              </Button>
            </CardContent>
          </Card>
        </div>

        {/* Right column — Looks Gallery */}
        <div className="space-y-4">
          <Card>
            <CardContent className="p-5">
              {/* Centered tabs */}
              <div className="flex justify-center gap-2 mb-4">
                {tabs.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setActiveTab(t.key)}
                    className={`px-4 py-2 text-sm rounded-full transition-colors ${
                      activeTab === t.key
                        ? "bg-accent text-white"
                        : "bg-surface text-text-muted hover:text-text"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {/* Gallery / Voice Corpus */}
              {activeTab === "voice" ? (
                <VoiceCorpusTab avatarId={avatarId!} />
              ) : looksLoading ? (
                <div className="flex items-center gap-2 py-6 justify-center text-text-muted text-sm">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading looks...
                </div>
              ) : activeTab === "body_motion" ? (
                /* Body Motion: always show 6 pose slots.
                   Note: pose generation used to be gated on avatar.status ===
                   APPROVED, but the backend has no such requirement and the
                   gate confused users ("why can't I regenerate?"). We let any
                   avatar that has a face image generate poses; the cost is
                   metered the same way as before. */
                <div className="space-y-4">
                  {/* Body description editor */}
                  <div className="p-3 bg-white/5 rounded-lg">
                    <label className="text-[11px] text-white/40 uppercase tracking-wider">Body description</label>
                    <textarea
                      value={bodyDescValue}
                      onChange={(e) => setBodyDescValue(e.target.value)}
                      onBlur={handleBodyDescSave}
                      className="mt-1 w-full bg-transparent text-sm text-white/70 resize-none outline-none border-none focus:ring-0 placeholder:text-white/30"
                      rows={3}
                      placeholder="Describe the avatar's body appearance — build, posture, clothing, accessories. Used to generate the 6 angle poses below."
                    />
                    <div className="flex justify-end mt-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={handleRegeneratePoses}
                        disabled={!avatar?.face_ref_key || !bodyDescValue.trim()}
                        title={
                          !avatar?.face_ref_key
                            ? "Avatar has no face image yet"
                            : !bodyDescValue.trim()
                              ? "Add a body description first"
                              : "Regenerate all 6 angle poses from this description"
                        }
                        className="text-xs"
                      >
                        <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Regenerate poses
                      </Button>
                    </div>
                  </div>
                  <GenerateAllMissingButton
                    avatarId={avatarId!}
                    existingLooks={filteredLooks}
                    disabled={!avatar?.face_ref_key}
                  />
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  {ALL_POSES.map((pose) => {
                    const existing = filteredLooks.find((l) => l.pose_angle === pose.value);
                    if (existing) {
                      return (
                        <LookCard
                          key={pose.value}
                          look={existing}
                          onPreview={() => setPreviewLook(existing)}
                          onSetDefault={() => setDefaultMutation.mutate(existing.id)}
                          onDelete={() => deleteLookMutation.mutate(existing.id)}
                          onRegenerate={() => {
                            deleteLookMutation.mutate(existing.id);
                            setTimeout(() => {
                              avatarLooksApi.create(avatarId!, {
                                name: `AI: ${pose.label}`,
                                look_type: "body_motion",
                                pose_angle: pose.value,
                              }).then(() => qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] }));
                            }, 1000);
                          }}
                        />
                      );
                    }
                    return (
                      <div
                        key={pose.value}
                        className="border-2 border-dashed border-border/50 rounded-xl p-4 flex flex-col items-center justify-center gap-2 min-h-[200px]"
                      >
                        <span className="text-sm text-text-muted">{pose.label}</span>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!avatar?.face_ref_key}
                          title={!avatar?.face_ref_key ? "Avatar has no face image yet" : undefined}
                          onClick={() => {
                            avatarLooksApi.create(avatarId!, {
                              name: `AI: ${pose.label}`,
                              look_type: "body_motion",
                              pose_angle: pose.value,
                            }).then(() => qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] }));
                          }}
                        >
                          Generate
                        </Button>
                      </div>
                    );
                  })}
                </div>
                </div>
              ) : filteredLooks.length === 0 ? (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                  {/* Dashed "Add new" placeholder */}
                  <div
                    className="border-2 border-dashed border-border/50 rounded-xl p-4 flex flex-col items-center justify-center gap-2 min-h-[200px] cursor-pointer hover:border-accent/50 transition-colors"
                    onClick={() => setAddOpen(true)}
                  >
                    <Plus className="h-8 w-8 text-text-muted" />
                    <span className="text-sm text-text-muted">Add Look</span>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                  {filteredLooks.map((look) => (
                    <LookCard
                      key={look.id}
                      look={look}
                      onPreview={() => setPreviewLook(look)}
                      onSetDefault={() => setDefaultMutation.mutate(look.id)}
                      onDelete={() => deleteLookMutation.mutate(look.id)}
                    />
                  ))}
                  {/* Dashed "Add new" placeholder at end */}
                  <div
                    className="border-2 border-dashed border-border/50 rounded-xl p-4 flex flex-col items-center justify-center gap-2 min-h-[200px] cursor-pointer hover:border-accent/50 transition-colors"
                    onClick={() => setAddOpen(true)}
                  >
                    <Plus className="h-8 w-8 text-text-muted" />
                    <span className="text-sm text-text-muted">Add Look</span>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Style DNA — paste 1-3 creator video URLs and clone their
              voice + editing style for future cast generations. */}
          <StyleDNA
            avatar={avatar}
            onAnalyzed={() => qc.invalidateQueries({ queryKey: ["avatar-status", avatarId] })}
          />

          {/* Past Live Sessions — teach this avatar your live selling style. */}
          <div className="rounded-xl border border-border bg-card p-5">
            <LiveReferenceCard
              avatarId={avatarId!}
              title="Live Voice"
              subtitle="Upload past live sessions to teach this avatar your selling style."
            />
          </div>
        </div>
      </div>

      {/* Add Look Dialog */}
      <AddLookDialog
        avatarId={avatarId!}
        open={addOpen}
        onOpenChange={setAddOpen}
        lookType={activeTab === "voice" ? "background" : activeTab}
      />

      {/* Preview Modal */}
      {previewLook && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80" onClick={() => setPreviewLook(null)}>
          <div className="relative max-w-lg w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <button onClick={() => setPreviewLook(null)} className="absolute -top-10 right-0 text-white/70 hover:text-white">
              <X className="h-6 w-6" />
            </button>
            {previewLook.image_url && (
              <img src={previewLook.image_url} alt={previewLook.name} className="w-full rounded-lg" />
            )}
            <div className="mt-3 text-center">
              <p className="text-white font-medium">{previewLook.name}</p>
              {previewLook.is_default && (
                <p className="text-yellow-400 text-sm mt-1">This is your avatar's default appearance</p>
              )}
              {previewLook.pose_angle && (
                <Badge className="mt-1" variant="secondary">{POSE_LABELS[previewLook.pose_angle] || previewLook.pose_angle}</Badge>
              )}
            </div>
          </div>
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
              style={{ aspectRatio: "9/16" }}
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

function GenerateAllMissingButton({
  avatarId,
  existingLooks,
  disabled: externalDisabled,
}: {
  avatarId: string;
  existingLooks: AvatarLook[];
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState("");

  const ALL_ANGLES = ["front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"];
  const coveredAngles = new Set(
    existingLooks
      .filter((l) => l.pose_angle && (l.status === "ready" || l.status === "pending" || l.status === "generating"))
      .map((l) => l.pose_angle)
  );
  const missingCount = ALL_ANGLES.filter((a) => !coveredAngles.has(a)).length;

  const readyCount = existingLooks.filter((l) => l.status === "ready").length;
  const pendingCount = existingLooks.filter((l) => l.status === "pending" || l.status === "generating").length;

  useEffect(() => {
    if (generating && pendingCount > 0) {
      setProgress(`Generating ${readyCount}/6 poses...`);
    } else if (generating && pendingCount === 0 && readyCount > 0) {
      setProgress("All poses generated!");
      setTimeout(() => { setGenerating(false); setProgress(""); }, 3000);
    }
  }, [generating, readyCount, pendingCount]);

  if (missingCount === 0 && !generating) return null;

  const handleGenerate = async () => {
    setGenerating(true);
    setProgress("Queuing all missing poses...");
    try {
      const result = await avatarLooksApi.generateAllBodyMotion(avatarId);
      setProgress(`Generating 0/${result.total_poses} poses...`);
      qc.invalidateQueries({ queryKey: ["avatar-looks", avatarId] });
      toast({ title: `Generating ${result.created} missing poses`, description: "This may take a few minutes." });
    } catch (err: any) {
      toast({
        title: "Failed to start generation",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
      setGenerating(false);
      setProgress("");
    }
  };

  return (
    <div className="text-center">
      <Button size="sm" variant="outline" onClick={handleGenerate} disabled={generating || missingCount === 0 || externalDisabled} className="text-xs" title={externalDisabled ? "Approve avatar first" : undefined}>
        {generating ? (
          <><Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />{progress || "Generating..."}</>
        ) : (
          <><Sparkles className="mr-1.5 h-3.5 w-3.5" />Generate All Missing ({missingCount})</>
        )}
      </Button>
    </div>
  );
}

function LookCard({
  look,
  onPreview,
  onSetDefault,
  onDelete,
  onRegenerate,
}: {
  look: AvatarLook;
  onPreview: () => void;
  onSetDefault: () => void;
  onDelete: () => void;
  onRegenerate?: () => void;
}) {
  return (
    <div className="border border-border rounded-lg overflow-hidden group">
      <button onClick={onPreview} className="relative aspect-9/16 bg-black w-full overflow-hidden">
        {look.image_url && look.status === "ready" ? (
          <img
            src={look.image_url}
            alt={look.name}
            className="w-full h-full object-cover transition-transform duration-500 ease-out group-hover:scale-110"
          />
        ) : look.status === "generating" || look.status === "pending" ? (
          <div className="w-full h-full flex items-center justify-center text-text-muted">
            <Loader2 className="w-6 h-6 animate-spin" />
          </div>
        ) : (
          <div className="w-full h-full flex items-center justify-center text-red-400 text-xs p-2 text-center">
            Failed: {look.error_message}
          </div>
        )}
        {look.is_default && (
          <div className="absolute top-1 right-1 bg-yellow-500 text-black text-[10px] px-1.5 py-0.5 rounded flex items-center">
            <Star className="w-2.5 h-2.5 mr-0.5 fill-current" /> Default
          </div>
        )}
        {look.is_original && (
          <div className="absolute top-1 left-1 bg-blue-600 text-white text-[9px] px-1.5 py-0.5 rounded font-medium">
            Original
          </div>
        )}
        {look.pose_angle && (
          <div className="absolute bottom-1 left-1 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded">
            {POSE_LABELS[look.pose_angle] || look.pose_angle}
          </div>
        )}
        {look.name.startsWith("Auto:") && (
          <div className="absolute top-1 left-1 bg-blue-900/60 text-blue-300 text-[9px] px-1.5 py-0.5 rounded">
            Auto-extracted
          </div>
        )}
        {look.look_type === "tryon" && (
          <div className="absolute bottom-1 left-1 bg-purple-600/80 text-white text-[10px] px-1.5 py-0.5 rounded">
            Try-On
          </div>
        )}
        {/* Prominent in-image Regenerate overlay. Always faintly visible so
            the user knows it's there; brightens + expands its label on
            hover. Visible only when the look has actually rendered —
            generating / failed slots use other UI. */}
        {onRegenerate && look.status === "ready" && (
          <div
            role="button"
            tabIndex={0}
            aria-label="Regenerate this image"
            onClick={(e) => {
              e.stopPropagation();
              onRegenerate();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.stopPropagation();
                e.preventDefault();
                onRegenerate();
              }
            }}
            className="absolute top-2 right-2 inline-flex items-center gap-1.5 rounded-full bg-black/55 backdrop-blur-md text-white text-[11px] font-medium px-2 py-1.5 ring-1 ring-white/15 shadow-md opacity-70 hover:opacity-100 hover:bg-accent/85 hover:ring-accent/60 transition-all cursor-pointer"
            title="Regenerate this image"
          >
            <RefreshCw className="w-3.5 h-3.5 transition-transform group-hover:rotate-180 duration-500" />
            <span className="max-w-0 overflow-hidden whitespace-nowrap group-hover:max-w-[120px] transition-[max-width] duration-300">
              Regenerate
            </span>
          </div>
        )}
      </button>
      <div className="p-2">
        <div className="text-xs font-medium truncate">{look.name}</div>
        <div className="flex items-center gap-1.5 mt-1.5">
          <Badge variant="secondary" className="text-[9px] px-1 py-0">
            {look.status}
          </Badge>
          <div className="flex-1" />
          {!look.is_default && look.status === "ready" && (
            <button
              onClick={(e) => { e.stopPropagation(); onSetDefault(); }}
              className="text-text-muted hover:text-text" title="Set as default"
            >
              <Star className="w-3.5 h-3.5" />
            </button>
          )}
          {/* Bottom-row regenerate icon was deduped — the prominent
              in-image overlay above (top-right of the thumbnail) is now
              the single regenerate affordance. */}
          {!look.is_default && !look.is_original && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              className="text-red-500 hover:text-red-400" title="Delete"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

    </div>
  );
}

/**
 * In-image Regenerate pill for the avatar profile photo / video poster.
 *
 * Mirrors the body-shot LookCard overlay so the entire Edit-Avatar page
 * has a consistent "hover an image, see Regenerate" affordance. Always
 * faintly visible (opacity-70) so users know it's there; brightens and
 * expands its label on hover.
 */
function ProfileRegenerateOverlay({
  visible,
  onRegenerate,
}: {
  visible: boolean;
  onRegenerate: () => void;
}) {
  if (!visible) return null;
  return (
    <button
      type="button"
      aria-label="Regenerate avatar"
      onClick={(e) => {
        e.stopPropagation();
        onRegenerate();
      }}
      className="absolute top-2 right-2 inline-flex items-center gap-1.5 rounded-full bg-black/55 backdrop-blur-md text-white text-[11px] font-medium px-2 py-1.5 ring-1 ring-white/15 shadow-md opacity-70 hover:opacity-100 hover:bg-accent/85 hover:ring-accent/60 transition-all cursor-pointer z-10"
      title="Regenerate avatar"
    >
      <RefreshCw className="w-3.5 h-3.5 transition-transform group-hover:rotate-180 duration-500" />
      <span className="max-w-0 overflow-hidden whitespace-nowrap group-hover:max-w-[120px] transition-[max-width] duration-300">
        Regenerate
      </span>
    </button>
  );
}
