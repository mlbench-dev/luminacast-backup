// ── Avatar ──

import type {
  Avatar,
  EditFrameResponse,
  FaceCandidatesResponse,
  FetchVideosResponse,
  UploadFaceResponse,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const avatarApi = {
  list: (params?: { status?: string }) =>
    api.get<{ avatars: Avatar[]; total: number }>("/avatar/list", { params }).then((r) => r.data),
  cloneFromTikTok: (data: { tiktok_url?: string; name?: string; test_script?: string; consent_confirmed: boolean }) =>
    api.post<Avatar>("/avatar/clone-from-tiktok", data).then((r) => r.data),
  cloneWithMedia: (data: {
    photo?: File; audio?: File; name?: string;
    test_script?: string; tiktok_url?: string; consent_confirmed: boolean;
  }) => {
    const formData = new FormData();
    if (data.photo) formData.append("photo", data.photo);
    if (data.audio) formData.append("audio", data.audio);
    formData.append("name", data.name || "");
    formData.append("test_script", data.test_script || "");
    formData.append("tiktok_url", data.tiktok_url || "");
    formData.append("consent_confirmed", String(data.consent_confirmed));
    return api.post<Avatar>("/avatar/clone-with-media", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  status: (id: string) =>
    api.get<Avatar>(`/avatar/status/${id}`).then((r) => r.data),
  approve: (id: string) =>
    api.post<Avatar>(`/avatar/${id}/approve`).then((r) => r.data),
  regenerate: (id: string, testScript?: string) =>
    api.post<Avatar>(`/avatar/${id}/regenerate`, { test_script: testScript }).then((r) => r.data),
  update: (id: string, data: { name?: string }) =>
    api.put(`/avatar/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/avatar/${id}`),
  getCandidates: (id: string) =>
    api.get<{ avatar_id: string; status: string; candidate_frames: string[] }>(`/avatar/${id}/candidates`).then((r) => r.data),
  selectFrame: (id: string, frameUrl: string) =>
    api.post<Avatar>(`/avatar/${id}/select-frame`, { frame_url: frameUrl }).then((r) => r.data),
  captureFrame: (id: string, frameBlob: Blob) => {
    const formData = new FormData();
    formData.append("frame", frameBlob, "captured_frame.jpg");
    return api.post<Avatar>(`/avatar/${id}/capture-frame`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  createAvatar: (data: { tiktok_url?: string; name: string }) =>
    api.post<{ avatar_id: string }>("/avatar/create", data).then((r) => r.data),
  selectFace: (id: string, faceUrl: string) =>
    api.post<{ status: string }>(`/avatar/${id}/select-face`, { face_url: faceUrl }).then((r) => r.data),
  fetchVideos: (data: { tiktok_url?: string; name: string; page?: number; per_page?: number }) =>
    api.post<FetchVideosResponse>("/avatar/fetch-videos", { ...data, page: data.page || 1, per_page: data.per_page || 16 }, { timeout: 120000 }).then((r) => r.data),
  processSegment: (id: string, data: { video_r2_key: string; start_seconds: number; end_seconds: number }) =>
    api.post<{ status: string }>(`/avatar/${id}/process-segment`, data).then((r) => r.data),
  getFaceCandidates: (id: string) =>
    api.get<FaceCandidatesResponse>(`/avatar/${id}/face-candidates`).then((r) => r.data),
  editFrame: (id: string, data: { frame_url: string; instructions: string }) =>
    api.post<EditFrameResponse>(`/avatar/${id}/edit-frame`, data).then((r) => r.data),
  uploadFace: (id: string, imageBlob: Blob) => {
    const formData = new FormData();
    formData.append("file", imageBlob, "manual_face.jpg");
    return api.post<UploadFaceResponse>(`/avatar/${id}/upload-face`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  uploadFrame: (id: string, frameBlob: Blob) => {
    const formData = new FormData();
    formData.append("file", frameBlob, `manual_frame_${Date.now()}.jpg`);
    return api.post<{ frame_url: string; r2_key: string }>(`/avatar/${id}/upload-frame`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  recloneVoice: (id: string) =>
    api.post<{ status: string; message: string }>(`/avatar/${id}/reclone-voice`).then((r) => r.data),
  analyzeStyle: (id: string, urls: string[]) =>
    api.post<{
      style_dna: import("@/lib/types").StyleDNAResult;
      voice_duration_s: number;
      successful_videos: number;
      requested_videos: number;
    }>(`/avatar/${id}/analyze-style`, { urls }, { timeout: 600000 }).then((r) => r.data),
  resetStyleDNA: (id: string) =>
    api.delete<{ ok: boolean }>(`/avatar/${id}/style-dna`).then((r) => r.data),
  downloadVideo: (data: { web_video_url: string }) =>
    api.post<{ video_url: string; video_r2_key: string; duration_seconds: number }>("/avatar/download-video", data).then((r) => r.data),
  uploadVideo: async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post<{ video_r2_key: string; video_url: string; duration_seconds: number }>("/avatar/upload-video", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },

  // ── Pipeline resume (Phase F) ──
  getRenderJobs: (avatarId: string) =>
    api.get<{
      avatar_id: string;
      avatar_status: string;
      avatar_phase: string;
      steps: Record<string, {
        job_id: string | null;
        state: string;
        error_message: string | null;
        created_at: string | null;
        started_at: string | null;
        completed_at: string | null;
        progress_percent: number | null;
        label: string;
        default_eta_seconds: number;
      }>;
      overall: string;
      failed_step: string | null;
      running_step: string | null;
      pipeline_type: string;
    }>(`/avatar/${avatarId}/render-jobs`).then((r) => r.data),
  resumePipeline: (avatarId: string) =>
    api.post<{ resumed_from_step: string; render_job_id: string; avatar_id: string } | { status: string; avatar_id: string }>(`/avatar/${avatarId}/resume-pipeline`).then((r) => r.data),

  // ── Clone Scout ──
  scoutStart: (tiktokHandle: string) =>
    api.post<{ scan_id: string; status: string }>("/avatar/clone/scout", { tiktok_handle: tiktokHandle }).then((r) => r.data),
  scoutStatus: (scanId: string) =>
    api.get<any>(`/avatar/clone/scout/${scanId}`).then((r) => r.data),
  scoutSelectVideo: (scanId: string, data: { tiktok_video_id: string; segment_start_ms: number; segment_end_ms: number }) =>
    api.post<{ avatar_id: string; status: string }>(`/avatar/clone/scout/${scanId}/select-video`, data).then((r) => r.data),

  // ── Clone Pipeline (unified flow) ──
  cloneCreate: (data: {
    name?: string; target_audience?: Record<string, unknown>; gender?: string;
    description?: string; body_description?: string; style_preset?: string; imperfections?: string[];
  }) => api.post<{ avatar_id: string }>("/avatar/clone/create", data).then((r) => r.data),

  cloneUploadFace: (avatarId: string, file: File, extractVoice?: boolean) => {
    const formData = new FormData();
    formData.append("avatar_id", avatarId);
    formData.append("file", file, file.name);
    if (extractVoice) formData.append("extract_voice", "true");
    return api.post<{
      candidates: { url: string; r2_key: string; score: number }[];
      source_type: "image" | "video";
      voice_corpus_entry_id?: string;
      voice_extraction?: "processing" | "failed";
    }>("/avatar/clone/upload-face", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000,
    }).then((r) => r.data);
  },

  cloneUploadVoice: (avatarId: string, file: File) => {
    const formData = new FormData();
    formData.append("avatar_id", avatarId);
    formData.append("file", file, file.name);
    return api.post<{ corpus_entry_id: string; status: string }>("/avatar/clone/upload-voice", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000,
    }).then((r) => r.data);
  },

  cloneSelectFace: (avatarId: string, faceUrl: string, r2Key?: string) =>
    api.patch<{ status: string; face_ref_key: string; face_url: string }>(
      `/avatar/clone/${avatarId}/select-face`,
      { face_url: faceUrl, r2_key: r2Key },
    ).then((r) => r.data),

  cloneGenerate: (avatarId: string, testScript?: string) =>
    api.post<{ status: string; avatar_id: string }>(
      `/avatar/clone/${avatarId}/generate`,
      { test_script: testScript },
    ).then((r) => r.data),

  cloneDescribeFace: (avatarId: string, faceImageUrl: string) =>
    api.post<{ name: string; description: string; body_description: string }>(
      "/avatar/clone/describe-face",
      { avatar_id: avatarId, face_image_url: faceImageUrl },
      { timeout: 60000 },
    ).then((r) => r.data),

  // ── AI Avatar (Path 4) ──

  // New pipeline endpoints
  aiGenerateDescription: (hint: string) =>
    api.post<{ description: string; suggested_name: string }>("/avatar/ai/generate-description", { hint }).then((r) => r.data),
  aiGenerateVoiceDescription: (
    avatarId: string,
    opts?: { gender?: string; language?: string; accent?: string; baseVoiceId?: string; baseVoiceName?: string; baseVoiceDescriptor?: string },
  ) =>
    api.post<{ voice_description: string; test_speech: string; suggested_filters: { gender: string; language: string; tags: string[] } }>("/avatar/ai/generate-voice-description", {
      avatar_id: avatarId,
      gender: opts?.gender || "",
      language: opts?.language || "",
      accent: opts?.accent || "",
      base_voice_id: opts?.baseVoiceId || "",
      base_voice_name: opts?.baseVoiceName || "",
      base_voice_descriptor: opts?.baseVoiceDescriptor || "",
    }).then((r) => r.data),
  aiGenerateVoicePreviews: (avatarId: string, voiceDescription: string, testSpeech: string, gender?: string, language?: string, accent?: string) =>
    api.post<{ previews: Array<{ preview_id: string; audio_url: string; index: number }> }>("/avatar/ai/generate-voice-previews", { avatar_id: avatarId, voice_description: voiceDescription, test_speech: testSpeech, gender: gender || "", language: language || "", accent: accent || "" }).then((r) => r.data),
  aiApproveVoice: (avatarId: string, previewId: string) =>
    api.post<{ voice_id: string; status: string }>("/avatar/ai/approve-voice", { avatar_id: avatarId, preview_id: previewId }).then((r) => r.data),

  createAIAvatar: (data: { name?: string }) =>
    api.post<{ avatar_id: string }>("/avatar/ai/create", data).then((r) => r.data),
  getSlotSummary: () =>
    api.get<{ included: number; purchased: number; total: number; used: number; remaining: number }>("/avatar/slots").then((r) => r.data),
  aiGenerateFaces: async (avatarId: string, data: { description: string; reference_photo?: File }) => {
    if (data.reference_photo) {
      // Upload reference photo first, then pass URL
      const formData = new FormData();
      formData.append("file", data.reference_photo);
      const uploadResult = await api.post<{ face_ref_key: string; face_url: string }>(`/avatar/${avatarId}/upload-face`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      }).then((r) => r.data);
      return api.post<{ face_urls: string[] }>("/avatar/ai/generate-faces", {
        description: data.description,
        reference_photo_url: uploadResult.face_url,
      }).then((r) => r.data);
    }
    return api.post<{ face_urls: string[] }>("/avatar/ai/generate-faces", {
      description: data.description,
    }).then((r) => r.data);
  },
  aiEditFace: (avatarId: string, data: { face_url: string; instructions: string }) =>
    api.post<{ original_url: string; edited_url: string }>("/avatar/ai/edit-face", data).then((r) => r.data),
  aiSelectFace: (avatarId: string, data: { face_url: string }) =>
    api.post(`/avatar/ai/${avatarId}/select-face`, data).then((r) => r.data),
  aiGetVoices: (params: { page?: number; per_page?: number; gender?: string; search?: string }) =>
    api.get<{ voices: Array<{ voice_id: string; name: string; description: string; gender: string; sample_url: string; tags: string[]; usage_count: number }>; total: number }>("/avatar/ai/voices", { params }).then((r) => r.data),
  aiPreviewVoice: (avatarId: string, data: { voice_id: string; text: string; speed?: number }) =>
    api.post<{ audio_url: string }>(`/avatar/ai/${avatarId}/preview-voice`, data).then((r) => r.data),
  aiSelectVoice: (avatarId: string, data: { voice_id: string; speed?: number }) =>
    api.post(`/avatar/ai/${avatarId}/select-voice`, data).then((r) => r.data),
  aiGeneratePreview: (avatarId: string, data: { test_script?: string }) =>
    api.post(`/avatar/ai/${avatarId}/generate-preview`, data).then((r) => r.data),
  // Avatar Pipeline Overhaul — new endpoints
  aiRewriteDescription: (data: { avatar_id: string; target_audience?: Record<string, unknown>; base_description: string; style_presets: string[]; imperfections: string[]; regenerate?: boolean }) =>
    api.post<{ description: string }>("/avatar/ai/rewrite-description", data).then((r) => r.data),
  updateAvatar: (avatarId: string, data: Record<string, unknown>) =>
    api.patch<{ id: string }>(`/avatar/${avatarId}`, data).then((r) => r.data),
  
  aiGenerateBodyDescription: (avatarId: string) =>
    api.post<{ body_description: string }>("/avatar/ai/generate-body-description", { avatar_id: avatarId }).then((r) => r.data),
  // POST kicks off the async pipeline and returns immediately with set_id +
  // status='running'. The blocking sync version was being killed at
  // Cloudflare's ~100s edge timeout. Use aiGetBodyShotSet to poll for the
  // final angles/validation.
  aiGenerateBodyShots: (avatarId: string) =>
    api.post<{
      set_id: string;
      status: "running" | "completed" | "failed";
    }>("/avatar/ai/generate-body-shots", { avatar_id: avatarId }).then((r) => r.data),
  aiGetBodyShotSet: (setId: string) =>
    api.get<{
      set_id: string;
      status: "running" | "completed" | "failed";
      error?: string;
      angles?: Record<string, string>;
      front_shot_url?: string;
      canonical_url?: string;
      validation?: Record<string, { expected: string; classified: string; match: boolean }>;
      description_used?: string;
    }>(`/avatar/ai/body-shot-sets/${setId}`).then((r) => r.data),
  // Returns the most recent BodyShotSet for an avatar, or {set: null}
  // when the avatar has never run the body-shot pipeline. The Setup
  // wizard checks this on mount to decide whether to kick a NEW job
  // or just hydrate the UI from the existing one.
  aiGetLatestBodyShotSet: (avatarId: string) =>
    api.get<{
      set: null | {
        set_id: string;
        status: "running" | "completed" | "failed";
        created_at?: string | null;
        error?: string;
        angles?: Record<string, string>;
        front_shot_url?: string;
        canonical_url?: string;
        validation?: Record<string, { expected: string; classified: string; match: boolean }>;
        description_used?: string;
      };
    }>(`/avatar/${avatarId}/latest-body-shot-set`).then((r) => r.data),
  aiRegenerateBodyShot: (avatarId: string, setId: string, angle: string) =>
    api.post<{
      angle: string;
      url: string;
      validation?: { expected: string; classified: string; match: boolean };
    }>("/avatar/ai/regenerate-body-shot", { avatar_id: avatarId, set_id: setId, angle }).then((r) => r.data),
  aiGetLockedVoiceAudio: (avatarId: string) =>
    api.get<{ audio_url: string; cached: boolean }>(`/avatar/${avatarId}/locked-voice-audio`).then((r) => r.data),
  aiSaveTargetAudience: (avatarId: string, targetAudience: Record<string, unknown>) =>
    api.post<{ status: string }>("/avatar/ai/save-target-audience", { avatar_id: avatarId, target_audience: targetAudience }).then((r) => r.data),
  aiLockTestScript: (avatarId: string, testScript: string) =>
    api.post<{ status: string; locked_test_script: string }>(`/avatar/ai/${avatarId}/lock-test-script`, { test_script: testScript }).then((r) => r.data),
  aiRegeneratePreviewVideo: (avatarId: string) =>
    api.post<{ preview_video_url: string; status: string }>(`/avatar/${avatarId}/regenerate-preview-video`).then((r) => r.data),
  aiSaveSetup: (avatarId: string, data: { target_audience: Record<string, unknown>; name: string; description: string; gender: string; body_description?: string; style_preset?: string; imperfections?: string[] }) =>
    api.post<{ status: string }>("/avatar/ai/save-setup", { avatar_id: avatarId, ...data }).then((r) => r.data),
  aiRewriteAudienceDescription: (data: { 
    age_min: number; 
    age_max: number; 
    gender_lean: number; 
    gender_doesnt_matter: boolean;
    interests: string[];
    geography: string;
    income_bracket: string;
    occupations: string[];
  }) =>
    api.post<{ description: string }>("/avatar/ai/rewrite-audience-description", data).then((r) => r.data),
  aiRewriteAvatarNameAndDescription: (data: { audience_description: string; gender: string; presets: string[]; imperfections: string[] }) =>
    api.post<{ name: string; description: string; body_description: string }>("/avatar/ai/rewrite-avatar-identity", data).then((r) => r.data),

  aiCloneVoice: async (avatarId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    return api.post<{ voice_id: string; voice_name: string }>(`/avatar/ai/${avatarId}/clone-voice`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },

};
