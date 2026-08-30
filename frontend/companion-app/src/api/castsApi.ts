// ── Casts ──

import type {
  Cast,
  CastCreate,
  CastListResponse,
  CastTemplate,
  EffectsConfig,
  GenerationStatus,
  OutlineResponse,
} from "@/lib/types";
import { api } from "@/lib/apiClient";
import type { MusicLibraryTrack } from "./aceStepApi";

export interface ReviewQueueCast {
  id: string;
  name?: string;
  description?: string;
  submitted_for_review_at: string | null;
  submitted_by: string | null;
  submitted_by_name: string | null;
}

export const castsApi = {
  list: (params?: {
    status?: string;
    has_render?: boolean;
    include_clips?: boolean;
    page?: number;
    per_page?: number;
  }) =>
    api
      .get<CastListResponse>("/casts", { params })
      .then((r) => r.data),
  /** Best-effort: returns {deleted, failed} rather than throwing, so a
   * partial batch (e.g. one cast started rendering mid-selection) still
   * removes everything else. */
  batchDelete: (castIds: string[]) =>
    api
      .post<{ deleted: string[]; failed: Record<string, string> }>(
        "/casts/batch-delete", { cast_ids: castIds },
      )
      .then((r) => r.data),
  /** Approve one of the LLM-suggested clips. Backend creates a child Cast
   * (clip_parent_cast_id + clip_block_ids) which renders via FFmpeg trim
   * of the parent's already-composed mp4 — no GPU work. */
  approveClip: (castId: string, clipIndex: number) =>
    api
      .post<{ child_cast_id: string; clip: any }>(
        `/casts/${castId}/clips/${clipIndex}/approve`,
      )
      .then((r) => r.data),
  /** Drop a suggested clip the user doesn't want. Approved clips
   * (already promoted to child casts) are unaffected. */
  dismissClip: (castId: string, clipIndex: number) =>
    api.delete(`/casts/${castId}/clips/${clipIndex}`).then((r) => r.data),
  get: (id: string) =>
    api.get<Cast>(`/casts/${id}`).then((r) => r.data),
  create: (data: CastCreate) =>
    api.post<Cast>("/casts", data).then((r) => r.data),
  update: (id: string, data: Partial<CastCreate>) =>
    api.put<Cast>(`/casts/${id}`, data).then((r) => r.data),
  patch: (id: string, data: Record<string, unknown>) =>
    api.patch<Cast>(`/casts/${id}`, data).then((r) => r.data),
  musicLibrary: () =>
    api
      .get<{ tracks: MusicLibraryTrack[] }>(`/casts/music-library`)
      .then((r) => r.data.tracks),
  /** Stage-1 creative templates the user can pick before writing a brief. */
  templates: () =>
    api
      .get<{ templates: CastTemplate[]; total: number }>(`/casts/templates`)
      .then((r) => r.data.templates),
  attachMusic: (
    id: string,
    url: string,
    mood?: string | null,
    tags?: string[] | null,
  ) =>
    api
      .patch<Cast>(`/casts/${id}`, {
        background_music_url: url,
        background_music_mood: mood ?? null,
        background_music_tags: tags ?? null,
      })
      .then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/casts/${id}`),
  retry: (castId: string) =>
    api.post(`/casts/${castId}/retry`).then((r) => r.data),
  retryVariant: (castId: string, variantId: string) =>
    api.post(`/casts/${castId}/variants/${variantId}/retry`).then((r) => r.data),
  generateOutline: (id: string) =>
    api.post<OutlineResponse>(`/casts/${id}/generate-outline`).then((r) => r.data),
  // Smart Cast outline: the LLM designs the whole video (categories + Pexels
  // stock + transitions). Returns expanded block dicts with category and
  // stock_media_url so the editor can show the right visual immediately.
  generateSmartOutline: (id: string) =>
    api.post<{
      cast_id: string;
      blocks: Array<Record<string, any>>;
      estimated_total_duration_seconds: number;
      estimated_cost_usd: number;
    }>(`/casts/${id}/generate-smart-outline`).then((r) => r.data),
  generateScripts: (id: string) =>
    api.post<{ cast_id: string; scripts_generated: number }>(`/casts/${id}/generate-scripts`).then((r) => r.data),
  pay: (id: string, paymentMethodId?: string) =>
    api.post<{ cast_id: string; status: string; payment_id: string }>(`/casts/${id}/pay`, { payment_method_id: paymentMethodId }).then((r) => r.data),
  generationStatus: (id: string) =>
    api.get<GenerationStatus>(`/casts/${id}/generation-status`).then((r) => r.data),
  saveLayout: (id: string, layoutConfig: Record<string, unknown>) =>
    api.put(`/casts/${id}/layout`, layoutConfig).then((r) => r.data),
  addBlock: (id: string, data: { block_type: string; product_id?: string; sort_order?: number; category?: string }) =>
    api.post(`/casts/${id}/blocks`, data).then((r) => r.data),
  updateBlock: (castId: string, blockId: string, data: Record<string, unknown>) =>
    api.put(`/casts/${castId}/blocks/${blockId}`, data).then((r) => r.data),
  deleteBlock: (castId: string, blockId: string) =>
    api.delete(`/casts/${castId}/blocks/${blockId}`),
  reorderBlocks: (castId: string, blockIds: string[]) =>
    api.patch(`/casts/${castId}/blocks/reorder`, { block_ids: blockIds }).then((r) => r.data),
  /** List the AI-generated start/end frames for a body-motion block. */
  listBodyMotionFrames: (castId: string, blockId: string) =>
    api
      .get<{
        block_id: string;
        selected_start_look_id: string | null;
        selected_end_look_id: string | null;
        start_prompt_seed: string | null;
        end_prompt_seed: string | null;
        frames: {
          start: Array<{
            id: string;
            avatar_id: string;
            name: string;
            face_ref_key: string | null;
            image_url: string | null;
            status: string;
            error_message: string | null;
            look_type: string;
            background_prompt: string | null;
            kind: "start" | "end";
            created_at: string | null;
          }>;
          end: Array<{
            id: string;
            avatar_id: string;
            name: string;
            face_ref_key: string | null;
            image_url: string | null;
            status: string;
            error_message: string | null;
            look_type: string;
            background_prompt: string | null;
            kind: "start" | "end";
            created_at: string | null;
          }>;
        };
      }>(`/casts/${castId}/blocks/${blockId}/body_motion_frames`)
      .then((r) => r.data),
  /** Generate a new AI start/end frame for a body-motion block from a prompt. */
  generateBodyMotionFrame: (
    castId: string,
    blockId: string,
    payload: { kind: "start" | "end"; prompt: string; regenerate?: boolean },
  ) =>
    api
      .post<{
        id: string;
        avatar_id: string;
        name: string;
        face_ref_key: string | null;
        image_url: string | null;
        status: string;
        error_message: string | null;
        look_type: string;
        background_prompt: string | null;
        kind: "start" | "end";
        block_id: string;
        created_at: string | null;
      }>(`/casts/${castId}/blocks/${blockId}/body_motion_frame`, payload)
      .then((r) => r.data),
  /** List the AI-generated start/end SCENE frames for an avatar_action block.
   *
   * Mirrors listBodyMotionFrames but reads frames keyed under the
   * `action_block_<id>_*` look_type prefix. The avatar's appearance is
   * auto-prepended on the server when the frames are generated.
   */
  listActionFrames: (castId: string, blockId: string) =>
    api
      .get<{
        block_id: string;
        selected_start_look_id: string | null;
        selected_end_look_id: string | null;
        start_prompt_seed: string | null;
        end_prompt_seed: string | null;
        frames: {
          start: Array<{
            id: string;
            avatar_id: string;
            name: string;
            face_ref_key: string | null;
            image_url: string | null;
            status: string;
            error_message: string | null;
            look_type: string;
            background_prompt: string | null;
            kind: "start" | "end";
            created_at: string | null;
          }>;
          end: Array<{
            id: string;
            avatar_id: string;
            name: string;
            face_ref_key: string | null;
            image_url: string | null;
            status: string;
            error_message: string | null;
            look_type: string;
            background_prompt: string | null;
            kind: "start" | "end";
            created_at: string | null;
          }>;
        };
      }>(`/casts/${castId}/blocks/${blockId}/action_frames`)
      .then((r) => r.data),
  /** Generate a new AI start/end SCENE frame for an avatar_action block. */
  generateActionFrame: (
    castId: string,
    blockId: string,
    payload: { kind: "start" | "end"; prompt: string; regenerate?: boolean },
  ) =>
    api
      .post<{
        id: string;
        avatar_id: string;
        name: string;
        face_ref_key: string | null;
        image_url: string | null;
        status: string;
        error_message: string | null;
        look_type: string;
        background_prompt: string | null;
        kind: "start" | "end";
        block_id: string;
        created_at: string | null;
      }>(`/casts/${castId}/blocks/${blockId}/action_frame`, payload)
      .then((r) => r.data),
  /** Delete one AI scene frame from an avatar_action block's carousel. */
  deleteActionFrame: (castId: string, blockId: string, frameId: string) =>
    api
      .delete<{ deleted: boolean; frame_id: string }>(
        `/casts/${castId}/blocks/${blockId}/action_frame/${frameId}`,
      )
      .then((r) => r.data),
  /** Upload first or last frame image for a generated_video block. */
  uploadBlockFrame: (castId: string, blockId: string, slot: "first" | "last", file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.post<{ r2_key: string; r2_url: string; slot: string; size_bytes: number }>(
      `/casts/${castId}/blocks/${blockId}/frames/upload?slot=${slot}`,
      fd,
      { headers: { "Content-Type": "multipart/form-data" } },
    ).then((r) => r.data);
  },
  generateTts: (castId: string) =>
    api.post(`/casts/${castId}/generate-tts`).then((r) => r.data),
  generateCaptions: (castId: string) =>
    api.post(`/casts/${castId}/generate-captions`).then((r) => r.data),
  generateVideos: (castId: string) =>
    api.post(`/casts/${castId}/generate-videos`).then((r) => r.data),
  ttsStatus: (castId: string) =>
    api.get(`/casts/${castId}/tts-status`).then((r) => r.data),
  toggleBlockActive: (castId: string, blockId: string, isActive: boolean) =>
    api.patch(`/casts/${castId}/blocks/${blockId}/active`, { is_active: isActive }).then((r) => r.data),
  addVariant: (castId: string, blockId: string, data: { script_text?: string; variant_label?: string }) =>
    api.post(`/casts/${castId}/blocks/${blockId}/variants`, data).then((r) => r.data),
  updateVariant: (castId: string, blockId: string, variantId: string, scriptText: string) =>
    api.put(`/casts/${castId}/blocks/${blockId}/variants/${variantId}`, { script_text: scriptText }).then((r) => r.data),
  generate: (id: string) =>
    api.post(`/casts/${id}/generate`).then((r) => r.data),
  saveBlocks: (id: string, blocks: Array<{ type: string; position: number; product_id?: string; mood?: string; script_text?: string; variants?: Array<{ script_text: string; variant_label: string }> }>) =>
    api.put<{ cast_id: string; blocks_saved: number }>(`/casts/${id}/blocks`, { blocks }).then((r) => r.data),
  saveEffects: (id: string, effectsConfig: EffectsConfig) =>
    api.put<{ status: string; effects_config: EffectsConfig }>(`/casts/${id}/effects`, effectsConfig).then((r) => r.data),
  uploadBackground: (id: string, file: File, mediaType: "image" | "video" = "image") => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post<{ key: string; url: string }>(`/casts/${id}/background?media_type=${mediaType}`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  recomposite: (castId: string) => api.post(`/casts/${castId}/recomposite`).then(r => r.data),
  finalize: (castId: string) =>
    api.post(`/casts/${castId}/finalize`).then(r => r.data),
  listRenders: (castId: string) =>
    api.get(`/casts/${castId}/renders`).then(r => r.data),
  getRender: (castId: string, renderId: string) =>
    api.get(`/casts/${castId}/renders/${renderId}`).then(r => r.data),
  selectRender: (castId: string, renderId: string) =>
    api.patch(`/casts/${castId}/renders/${renderId}/select`).then(r => r.data),
  cancelRender: (castId: string, renderId: string) =>
    api.post<{ id: string; status: string; error_message: string }>(
      `/casts/${castId}/renders/${renderId}/cancel`
    ).then(r => r.data),
  saveTimeline: (castId: string, payload: { variant_id: string; twick_data: any; block_regions: any[]; editor_state?: any }) =>
    api.put(`/casts/${castId}/timeline`, payload).then(r => r.data),
  getTimeline: (castId: string, variantId: string) =>
    api.get(`/casts/${castId}/timeline/${variantId}`).then(r => r.data),

  // Phase 2.2 — Per-block audio regeneration + variant management
  regenerateBlockAudio: (castId: string, blockId: string, scriptText: string, voiceSettings?: Record<string, unknown> | null) =>
    api.post<{ variant_id: string; audio_url: string; duration_seconds: number }>(
      `/casts/${castId}/blocks/${blockId}/regenerate-audio`,
      { script_text: scriptText, voice_settings: voiceSettings ?? null },
    ).then(r => r.data),
  listBlockVariants: (castId: string, blockId: string) =>
    api.get<{ variant_id: string; script_text: string; audio_url: string; duration_seconds: number; is_active: boolean; created_at: string }[]>(
      `/casts/${castId}/blocks/${blockId}/variants`,
    ).then(r => r.data),
  selectBlockVariant: (castId: string, blockId: string, variantId: string) =>
    api.patch<{ variant_id: string; audio_url: string; duration_seconds: number }>(
      `/casts/${castId}/blocks/${blockId}/select-variant`,
      { variant_id: variantId },
    ).then(r => r.data),

  // Phase 2.6 — Editor caption generation (multi-block, word-level)
  editorGenerateCaptions: (castId: string, audioSegments: { audio_url: string; block_id: string; audio_element_id: string; start_offset_s: number }[]) =>
    api.post<{
      results: {
        block_id: string;
        audio_element_id: string;
        captions: { text: string; startMs: number; endMs: number; timestampMs: number; confidence: number | null }[];
        words: { word: string; start: number; end: number; probability: number; block_id: string; source_audio_id: string }[];
        word_count: number;
        error?: string;
      }[];
    }>(
      `/casts/${castId}/editor-generate-captions`,
      { audio_segments: audioSegments },
    ).then(r => r.data),

  // Phase 2.4 — Cross-format cast duplication + sibling linkage
  duplicateAs: (castId: string, formatFamily: "horizontal" | "vertical") =>
    api.post<{ cast_id: string; format_family: string }>(
      `/casts/${castId}/duplicate-as`,
      { format_family: formatFamily },
    ).then(r => r.data),
  getSiblings: (castId: string) =>
    api.get<{
      self: { cast_id: string; format_family: string; name: string };
      siblings: { cast_id: string; format_family: string; name: string }[];
    }>(`/casts/${castId}/siblings`).then(r => r.data),
  estimateCost: (data: { duration_s: number; quality: string; layout: string }) =>
    api.post<{ cost_cents: number; breakdown: Record<string, number> }>(`/casts/estimate-cost`, data).then(r => r.data),

  // Cast versioning
  fork: (castId: string, name?: string) =>
    api.post<{ forked: boolean; old_version: number; new_version: number; version_id?: string }>(`/casts/${castId}/fork`, { name: name || "" }).then(r => r.data),
  listVersions: (castId: string) =>
    api.get<{ current_version: number; versions: Array<{ id: string; version: number; status_at_snapshot: string; name: string; block_count: number; duration_seconds: number | null; quality: string | null; created_at: string }> }>(`/casts/${castId}/versions`).then(r => r.data),
  getVersion: (castId: string, versionId: string) =>
    api.get<{ id: string; version: number; blocks_snapshot: any[]; status_at_snapshot: string; created_at: string }>(`/casts/${castId}/versions/${versionId}`).then(r => r.data),
  restoreVersion: (castId: string, versionId: string) =>
    api.post<{ restored: boolean; from_version: number; new_version: number }>(`/casts/${castId}/versions/${versionId}/restore`).then(r => r.data),

  // Phase 4.8.3 — Per-block render
  renderBlock: (castId: string, blockId: string, data: { render_action: string; avatar_angle?: string }) =>
    api.post<{ task_id: string; block_id: string; cost_cents: number; quality: string }>(
      `/casts/${castId}/blocks/${blockId}/render`, data,
    ).then(r => r.data),

  // Teams — review/approval workflow
  submitForReview: (castId: string) =>
    api.post<{ cast_id: string; approval_status: string }>(`/casts/${castId}/submit-for-review`).then(r => r.data),
  approveCast: (castId: string) =>
    api.post<{ cast_id: string; approval_status: string }>(`/casts/${castId}/approve`).then(r => r.data),
  rejectReview: (castId: string, reason?: string) =>
    api.post<{ cast_id: string; approval_status: string }>(`/casts/${castId}/reject-review`, { reason }).then(r => r.data),
  reviewQueue: () =>
    api.get<{ casts: ReviewQueueCast[] }>("/casts/review-queue").then(r => r.data.casts),
};
