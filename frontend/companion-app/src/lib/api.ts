import axios from "axios";
import { QueryClient } from "@tanstack/react-query";
import { Sentry } from "@/lib/sentry";
import type {
  TokenResponse,
  LoginRequest,
  RegisterRequest,
  ForgotPasswordRequest,
  ResetPasswordRequest,
  MessageResponse,
  User,
  Cast,
  CastCreate,
  CastListResponse,
  CastTemplate,
  OutlineResponse,
  GenerationStatus,
  Product,
  ProductCreate,
  StreamState,
  StreamStartRequest,
  Avatar,
  SessionSummary,
  VariantPerformance,
  AdminDashboard,
  AdminStream,
  AdminCreator,
  AdminPrompt,
  AdminPromptDetail,
  AdminUsageDaily,
  AdminUsageByUser,
  AdminUsageByService,
  AdminUsageRecent,
  AdminSystemStatus,
  ChatMessage,
  FetchVideosResponse,
  FaceCandidatesResponse,
  EditFrameResponse,
  UploadFaceResponse,
  TikTokShopProduct,
  LayoutTemplate,
  Channel,
  ProductWithAssets,
  ProductDetailResponse,
  ProductAsset,
  DiscoverResponse,
  CategoryTree,
  EffectsConfig,
  TeamRole,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";
console.log("BASE_URL", BASE_URL);
export const api = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
});

// Lightweight UUID v4 generator. crypto.randomUUID is widely available but
// fall back for older browsers / older WebViews still in the wild.
function uuidv4(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {}
  // RFC 4122 v4 fallback.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Per-tab session id. Persists across reloads via sessionStorage so
// audit-log can group everything from one tab without conflating tabs.
function getSessionId(): string {
  try {
    let id = sessionStorage.getItem("luminacast-session-id");
    if (!id) {
      id = uuidv4();
      sessionStorage.setItem("luminacast-session-id", id);
    }
    return id;
  } catch {
    return uuidv4();
  }
}

const MUTATING_METHODS = new Set(["post", "put", "patch", "delete"]);

// Attach JWT token + audit-log session/action IDs.
// Ensure token is always fresh from localStorage on each request.
api.interceptors.request.use((config) => {
  if (!config.headers["Authorization"]) {
    try {
      const raw = localStorage.getItem("luminacast-auth");
      if (raw) {
        const parsed = JSON.parse(raw);
        const token = parsed?.state?.token;
        if (token) {
          config.headers["Authorization"] = "Bearer " + token;
        }
      }
    } catch {}
  }

  // Tag every request with the per-tab session id so the audit log can
  // group actions; tag every mutation with a fresh action id so the UI
  // can correlate optimistic updates to their server-side event.
  try {
    config.headers["X-Session-Id"] = getSessionId();
    const method = (config.method || "get").toLowerCase();
    if (MUTATING_METHODS.has(method)) {
      config.headers["X-Action-Id"] = uuidv4();
    }
  } catch {}

  return config;
});

// Capture 5xx API errors to Sentry
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response && error.response.status >= 500) {
      Sentry.captureException(error, {
        extra: {
          url: error.config?.url,
          method: error.config?.method,
          status: error.response.status,
          data: error.response.data,
        },
      });
    }
    return Promise.reject(error);
  },
);

/**
 * Extracts a display-friendly string from an axios error's response body.
 * FastAPI returns `detail` as a plain string for custom HTTPExceptions, but
 * as an array of Pydantic error objects ({type, loc, msg, ...}) for raw
 * request-validation failures (422s) — rendering that array directly as JSX
 * throws "Objects are not valid as a React child".
 */
export function extractErrorMessage(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map((d) => (typeof d?.msg === "string" ? d.msg : String(d))).join(" ");
  }
  return fallback;
}

export function setAuthToken(token: string | null) {
  if (token) {
    api.defaults.headers.common["Authorization"] = `Bearer ${token}`;
  } else {
    delete api.defaults.headers.common["Authorization"];
  }
}

// React Query client
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

// ── Auth ──

export const authApi = {
  login: (data: LoginRequest) =>
    api.post<TokenResponse>("/auth/login", data).then((r) => r.data),
  register: (data: RegisterRequest) =>
    api.post<TokenResponse>("/auth/register", data).then((r) => r.data),
  me: () => api.get<User>("/auth/me").then((r) => r.data),
  forgotPassword: (data: ForgotPasswordRequest) =>
    api.post<MessageResponse>("/auth/forgot-password", data).then((r) => r.data),
  resetPassword: (data: ResetPasswordRequest) =>
    api.post<MessageResponse>("/auth/reset-password", data).then((r) => r.data),
};

// ── Teams ──

export interface TeamMemberDto {
  id: string;
  email: string;
  display_name?: string | null;
  role: TeamRole;
  status: "pending" | "active" | "revoked";
  invited_at: string;
  accepted_at?: string | null;
}

export interface WorkspaceOptionDto {
  owner_id: string;
  owner_label: string;
  role?: TeamRole | null;
  is_own: boolean;
}

export const teamsApi = {
  listMembers: () =>
    api.get<{ members: TeamMemberDto[] }>("/teams/members").then((r) => r.data.members),
  invite: (data: { email: string; role: TeamRole }) =>
    api.post<TeamMemberDto>("/teams/invite", data).then((r) => r.data),
  changeRole: (memberId: string, role: TeamRole) =>
    api.patch<TeamMemberDto>(`/teams/members/${memberId}/role`, { role }).then((r) => r.data),
  revoke: (memberId: string) =>
    api.delete(`/teams/members/${memberId}`).then((r) => r.data),
  previewInvite: (token: string) =>
    api
      .get<{ email: string; owner_label: string; role: TeamRole; requires_password: boolean }>(
        "/teams/accept-invite/preview",
        { params: { token } },
      )
      .then((r) => r.data),
  acceptInvite: (data: { token: string; password?: string }) =>
    api.post<TokenResponse>("/teams/accept-invite", data).then((r) => r.data),
  myWorkspaces: () =>
    api.get<{ workspaces: WorkspaceOptionDto[] }>("/teams/my-workspaces").then((r) => r.data.workspaces),
  switchWorkspace: (ownerId: string) =>
    api.post<TokenResponse>("/teams/switch-workspace", { owner_id: ownerId }).then((r) => r.data),
};

// ── User profile (custom interests, etc) ──
//
// Custom interests are stored as a per-user JSON array on the User model.
// The backend POST endpoint REPLACES the whole list, so callers append
// locally and re-send the full set whenever the user adds a new tag.
// Tags are normalized to lowercase server-side; the UI capitalizes for
// display only.
export const userApi = {
  getInterests: () =>
    api.get<{ custom_interests: string[] }>("/users/me/interests")
      .then((r) => r.data.custom_interests || []),
  setInterests: (interests: string[]) =>
    api.post<{ custom_interests: string[] }>("/users/me/interests", { interests })
      .then((r) => r.data.custom_interests || []),
  // Affiliate IDs (TikTok Shop, Amazon Associates). Empty / null `value`
  // clears the field — the backend treats whitespace-only strings the
  // same way to keep the UI free of trim() vs blanking edge cases.
  getAffiliates: () =>
    api.get<{ tiktok_affiliate_id: string | null; amazon_associate_tag: string | null }>(
      "/users/me/affiliates",
    ).then((r) => r.data),
  updateAffiliate: (platform: "tiktok" | "amazon", value: string | null) =>
    api.patch<{ tiktok_affiliate_id: string | null; amazon_associate_tag: string | null }>(
      "/users/me/affiliates",
      { platform, value },
    ).then((r) => r.data),
};

// ── Casts ──

export const castsApi = {
  list: (params?: { status?: string; has_render?: boolean; include_clips?: boolean }) =>
    api
      .get<CastListResponse>("/casts", { params })
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

export interface ReviewQueueCast {
  id: string;
  name?: string;
  description?: string;
  submitted_for_review_at: string | null;
  submitted_by: string | null;
  submitted_by_name: string | null;
}

// ── Products ──

export interface NeedsManualEntry {
  status: "needs_manual_entry";
  source: string;
  source_product_id: string | null;
  source_url: string;
  message: string;
}

export type FromUrlResult =
  | (ProductWithAssets & { already_existed?: boolean })
  | NeedsManualEntry;

export function isNeedsManualEntry(r: FromUrlResult): r is NeedsManualEntry {
  return (r as NeedsManualEntry)?.status === "needs_manual_entry";
}

export const productsApi = {
  list: (params?: { page?: number; per_page?: number; search?: string; sort?: string; filter?: string }) =>
    api.get<{ products: ProductWithAssets[]; total: number; page: number; per_page: number }>("/products", { params }).then((r) => r.data),
  get: (id: string) =>
    api.get<ProductDetailResponse>(`/products/${id}`).then((r) => r.data),
  create: (data: ProductCreate & Record<string, unknown>) =>
    api.post<ProductWithAssets>("/products", data).then((r) => r.data),
  // Returns the imported product on 201, or a needs_manual_entry payload on
  // 202 when the source (e.g. TikTok Shop) blocked the automated lookup.
  fromUrl: (url: string): Promise<FromUrlResult> =>
    api
      .post<ProductWithAssets & { already_existed?: boolean } | NeedsManualEntry>(
        "/products/from-url",
        { url },
      )
      .then((r) => {
        if (r.status === 202 && (r.data as NeedsManualEntry)?.status === "needs_manual_entry") {
          return r.data as NeedsManualEntry;
        }
        return r.data as ProductWithAssets & { already_existed?: boolean };
      }),
  refresh: (id: string) =>
    api.post<ProductWithAssets>(`/products/${id}/refresh`).then((r) => r.data),
  update: (id: string, data: Partial<ProductCreate> & Record<string, unknown>) =>
    api.put<ProductWithAssets>(`/products/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/products/${id}`),
  getAssets: (productId: string) =>
    api.get<ProductAsset[]>(`/products/${productId}/assets`).then((r) => r.data),
  uploadAsset: (productId: string, file: File, assetType: string = "product_shot") => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post<ProductAsset>(`/products/${productId}/assets/upload?asset_type=${assetType}`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  deleteAsset: (productId: string, assetId: string) =>
    api.delete(`/products/${productId}/assets/${assetId}`),
  generateOverlay: (productId: string) =>
    api.post(`/products/${productId}/generate-overlay`).then((r) => r.data),
  generateAiImages: (productId: string, style?: string, customPrompt?: string) =>
    api.post(`/products/${productId}/generate-ai-images`, { style: style || "product_shot", custom_prompt: customPrompt || "" }).then((r) => r.data),
  generateAiVideo: (productId: string, style?: string, duration?: number, customPrompt?: string, quality?: string) =>
    api.post(`/products/${productId}/generate-ai-video`, { style: style || "product_showcase", duration_seconds: duration || 5, custom_prompt: customPrompt || "", quality: quality || "pro" }).then((r) => r.data),
  removeBackground: (productId: string) =>
    api.post(`/products/${productId}/remove-background`).then((r) => r.data),
  importTiktok: (query: string, maxProducts?: number) =>
    api.post<{ products: TikTokShopProduct[]; total: number }>("/products/import-tiktok", { query, max_products: maxProducts || 50 }).then((r) => r.data),
  generateReviews: (productId: string) =>
    api.post<{
      reviews: Array<{ id: string; stars: number; author: string; text: string; verified: boolean; date: string }>;
      generated_at: string;
      model_used: string;
    }>(`/products/${productId}/generate-reviews`).then((r) => r.data),
};

// ── Channels ──

export const channelsApi = {
  list: (params?: { status?: string }) =>
    api.get<{ channels: Channel[]; total: number }>("/channels").then((r) => r.data),
  get: (id: string) =>
    api.get<Channel>(`/channels/${id}`).then((r) => r.data),
  create: (data: { platform: string; handle: string; display_name?: string; stream_key?: string; stream_url?: string }) =>
    api.post<Channel>("/channels", data).then((r) => r.data),
  update: (id: string, data: Partial<{ display_name: string; bio: string; handle: string; stream_key: string; stream_url: string }>) =>
    api.put<Channel>(`/channels/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/channels/${id}`),
  reindex: (id: string) =>
    api.post(`/channels/${id}/reindex`).then((r) => r.data),
  voiceProfile: (id: string) =>
    api.get(`/channels/${id}/voice-profile`).then((r) => r.data),
  transcripts: (id: string, page?: number, perPage?: number) =>
    api.get(`/channels/${id}/transcripts`, { params: { page: page || 1, per_page: perPage || 20 } }).then((r) => r.data),
};

// ── Stream ──

export const streamApi = {
  start: (data: StreamStartRequest) =>
    api.post<StreamState>("/stream/start", data).then((r) => r.data),
  stop: () =>
    api.post<StreamState>("/stream/stop").then((r) => r.data),
  skip: () =>
    api.post("/stream/skip").then((r) => r.data),
  pause: () =>
    api.post("/stream/pause").then((r) => r.data),
  pinConfirm: (productId: string) =>
    api.post("/stream/pin-confirm", { product_id: productId }).then((r) => r.data),
};

// ── Avatar ──

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

// ── Product Discovery ──

export const discoverApi = {
  trending: (params?: { section?: string; region?: string; page?: number; per_page?: number; sort_by?: string }) =>
    api.get<DiscoverResponse>("/discover/trending", { params }).then((r) => r.data),
  search: (params?: { q?: string; category?: string; min_price?: number; max_price?: number; min_revenue?: number; min_items_sold?: number; min_rating?: number; min_commission?: number; revenue_growth_min?: number; is_affiliate?: boolean; sort_by?: string; region?: string; page?: number; per_page?: number }) =>
    api.get<DiscoverResponse>("/discover/search", { params }).then((r) => r.data),
  categories: () =>
    api.get<{ categories: CategoryTree[] }>("/discover/categories").then((r) => r.data),
  importProduct: (tpId: string) =>
    api.post<{ product_id: string; message: string; already_imported: boolean }>(`/discover/import/${tpId}`).then((r) => r.data),
  refresh: (section?: string) =>
    api.post("/discover/refresh", null, { params: { section: section || "top_selling" } }).then((r) => r.data),
};

// ── TikTok Shop ──

export const tiktokShopApi = {
  search: (query: string, maxProducts: number = 50) =>
    api.post<{ products: TikTokShopProduct[]; total: number }>("/products/search-tiktok-shop", { query, max_products: maxProducts }).then((r) => r.data),
  proxyImage: (imageUrl: string) =>
    api.post<{ image_url: string; r2_key: string }>("/products/proxy-product-image", { image_url: imageUrl }).then((r) => r.data),
};

// ── Layout Templates ──

export const layoutsApi = {
  list: (params?: { status?: string }) =>
    api.get<{ templates: LayoutTemplate[]; total: number }>("/layout-templates").then((r) => r.data),
  create: (data: { name: string; config: Record<string, unknown> }) =>
    api.post<LayoutTemplate>("/layout-templates", data).then((r) => r.data),
  update: (id: string, data: { name: string; config: Record<string, unknown> }) =>
    api.put<LayoutTemplate>(`/layout-templates/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/layout-templates/${id}`),
};

// ── Script Rewrite ──

export const scriptApi = {
  rewrite: (castId: string, blockId: string, variantId: string, prompt: string) =>
    api.post<{ script_text: string }>(`/casts/${castId}/blocks/${blockId}/variants/${variantId}/rewrite`, { prompt }).then((r) => r.data),
  rewriteInVoice: (castId: string, blockId: string) =>
    api.post<{ original: string; rewritten: string; corpus_entries_used: number }>(`/casts/${castId}/blocks/${blockId}/rewrite-in-voice`).then((r) => r.data),
  refineAllBlocks: (castId: string, instruction: string) =>
    api.post<{ updated: { block_id: string; text: string }[] }>(`/casts/${castId}/refine-all-blocks`, { instruction }).then((r) => r.data),
};

// ── Analytics ──

export const analyticsApi = {
  sessions: () =>
    api.get<{ sessions: SessionSummary[]; total: number }>("/analytics/sessions").then((r) => r.data),
  session: (id: string) =>
    api.get<SessionSummary>(`/analytics/sessions/${id}`).then((r) => r.data),
  variants: () =>
    api.get<{ variants: VariantPerformance[]; total: number }>("/analytics/variants").then((r) => r.data),
};

// ── Chat ──

export const chatApi = {
  approve: (msgId: string, editedText?: string) =>
    api.post<ChatMessage>(`/chat/approve/${msgId}`, { edited_text: editedText ?? null }).then((r) => r.data),
  reject: (msgId: string) =>
    api.post<ChatMessage>(`/chat/reject/${msgId}`).then((r) => r.data),
  send: (sessionId: string, text: string, mode: "chat_only" | "voice_and_chat" = "chat_only") =>
    api.post("/chat/send", { session_id: sessionId, text, mode }).then((r) => r.data),
  lock: (msgId: string) =>
    api.post(`/chat/lock/${msgId}`).then((r) => r.data),
};

// ── Music ──

export interface MusicLibraryTrack {
  id: string;
  name: string;
  mood: string;
  url: string;
}

export interface MusicSoundCast {
  id: string;
  name: string;
  description: string;
  status: string;
  training_audio_count: number;
  training_audio_keys: string[];
  training_prompts: string[];
  training_steps: number;
  training_loss: number;
  training_error: string;
  lora_r2_key: string;
  created_at: string | null;
}

export interface MusicTrackItem {
  id: string;
  sound_cast_id: string;
  name: string;
  prompt: string;
  lyrics: string;
  status: string;
  audio_url: string;
  duration_seconds: number;
  generation_time_seconds: number;
  generation_error: string;
  seed: number;
  guidance_scale: number;
  inference_steps: number;
  scheduler_type: string;
  created_at: string | null;
}

// Legacy ACE-Step Sound Cast API. Kept for the parked "AI Music Studio"
// section behind ACE_STEP_ENABLED. The new Music page uses musicApi below.
export const aceStepApi = {
  listSoundCasts: () =>
    api.get<{ sound_casts: MusicSoundCast[] }>("/music/sound-casts").then((r) => r.data.sound_casts),
  getSoundCast: (id: string) =>
    api.get<MusicSoundCast>("/music/sound-casts/" + id).then((r) => r.data),
  createSoundCast: (data: { name: string; description?: string }) =>
    api.post<MusicSoundCast>("/music/sound-casts", data).then((r) => r.data),
  deleteSoundCast: (id: string) =>
    api.delete("/music/sound-casts/" + id),
  uploadTrainingAudio: (scId: string, file: File, prompt: string) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("prompt", prompt || "");
    return api.post<MusicSoundCast>("/music/sound-casts/" + scId + "/training-audio", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  deleteTrainingAudio: (scId: string, audioIdx: number) =>
    api.delete<MusicSoundCast>("/music/sound-casts/" + scId + "/training-audio/" + audioIdx).then((r) => r.data),
  startTraining: (scId: string, data: { steps?: number }) =>
    api.post<MusicSoundCast>("/music/sound-casts/" + scId + "/train", { training_prompts: [], steps: data.steps || 2400 }).then((r) => r.data),
  cancelTraining: (scId: string) =>
    api.post<MusicSoundCast>("/music/sound-casts/" + scId + "/cancel-training").then((r) => r.data),
  listTracks: (scId: string) =>
    api.get<{ tracks: MusicTrackItem[] }>("/music/sound-casts/" + scId + "/tracks").then((r) => r.data.tracks),
  generateTrack: (scId: string, data: {
    name: string; prompt: string; lyrics?: string; duration_seconds?: number;
    seed?: number; guidance_scale?: number; inference_steps?: number; scheduler_type?: string;
  }) =>
    api.post<MusicTrackItem>("/music/sound-casts/" + scId + "/tracks", data).then((r) => r.data),
  deleteTrack: (trackId: string) =>
    api.delete("/music/tracks/" + trackId),
};

// New AI music API (Browse / AI Generate / SFX / Uploaded).
export interface CatalogTrack {
  id: string;
  name: string;
  mood: string;
  intensity: string;
  tempo: string;
  duration: number;
  prompt: string;
  url: string;
  created_at: string | null;
}

export interface UploadedTrack {
  id: string;
  name: string;
  url: string;
  duration: number | null;
  file_size_bytes: number;
  created_at: string | null;
}

export interface SfxItem {
  key: string;
  icon: string;
  label: string;
  duration: number;
  description: string;
  url: string;
}

export interface AIGeneratedTrack {
  id: string;
  url: string;
  duration: number;
  prompt: string;
  mood?: string | null;
  intensity?: string | null;
  bpm?: number | null;
  key?: string | null;
}

export interface LibraryParamOption {
  label: string;
  value: string | number;
}

export interface LibraryParams {
  genres: LibraryParamOption[];
  moods: LibraryParamOption[];
  activities: LibraryParamOption[];
  bpms: LibraryParamOption[];
  durations: LibraryParamOption[];
}

export interface LibraryTrack {
  id: string;
  name: string;
  description: string;   // pre-formatted "120 BPM · C#m · high" line from backend
  mood: string;
  genre: string;
  moods: string[];
  genres: string[];
  intensity: string;
  mode: string;          // jingle | mix | track
  tempo: string;
  bpm: number | null;
  key: string;
  duration: number;
  prompt: string;
  url: string;
  created_at: string | null;
}

export interface LibraryTracksResponse {
  tracks: LibraryTrack[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export const musicApi = {
  catalog: (params?: { mood?: string; duration?: number; limit?: number }) =>
    api.get<{ tracks: CatalogTrack[]; total: number }>("/music/catalog", { params }).then((r) => r.data),
  libraryParams: () =>
    api.get<LibraryParams>("/music/library/params").then((r) => r.data),
  libraryTracks: (params: {
    genre?: string;
    mood?: string;
    activity?: string;
    bpm?: string;
    duration?: number;
    offset?: number;
    limit?: number;
  }) =>
    api
      .get<LibraryTracksResponse>("/music/library/tracks", { params })
      .then((r) => r.data),
  generate: (req: {
    prompt?: string;
    mood?: string;
    duration_seconds: number;
    intensity: string;
    cast_id?: string;
  }) => api.post<AIGeneratedTrack>("/music/ai/generate", req).then((r) => r.data),
  uploadedList: () =>
    api.get<{ tracks: UploadedTrack[]; total: number }>("/music/uploaded").then((r) => r.data),
  uploadedUpload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api
      .post<{ id: string; name: string; url: string; file_size_bytes: number }>(
        "/music/uploaded/upload",
        fd,
        { headers: { "Content-Type": "multipart/form-data" } },
      )
      .then((r) => r.data);
  },
  uploadedDelete: (id: string) =>
    api.delete<{ deleted: boolean }>("/music/uploaded/" + id).then((r) => r.data),
  sfxLibrary: () =>
    api.get<{ items: SfxItem[] }>("/music/sfx/library").then((r) => r.data),
};

// ── Admin ──

export const adminApi = {
  dashboard: () =>
    api.get<AdminDashboard>("/admin/dashboard").then((r) => r.data),
  streams: () =>
    api.get<{ active_streams: AdminStream[] }>("/admin/streams").then((r) => r.data),
  creators: () =>
    api.get<{ creators: AdminCreator[] }>("/admin/creators").then((r) => r.data),
  health: () =>
    api.get("/admin/health").then((r) => r.data),
  prompts: () =>
    api.get<{ prompts: AdminPrompt[]; total: number }>("/admin/prompts").then((r) => r.data),
  promptDetail: (name: string) =>
    api.get<AdminPromptDetail>(`/admin/prompts/${name}`).then((r) => r.data),
  updatePrompt: (name: string, data: { system_prompt: string; change_reason: string }) =>
    api.put(`/admin/prompts/${name}`, data).then((r) => r.data),
  revertPrompt: (name: string, version: number) =>
    api.post(`/admin/prompts/${name}/revert/${version}`).then((r) => r.data),
  usageDaily: (days?: number) =>
    api.get<AdminUsageDaily>("/admin/usage/daily", { params: { days: days || 30 } }).then((r) => r.data),
  usageByUser: (days?: number) =>
    api.get<AdminUsageByUser>("/admin/usage/by-user", { params: { days: days || 30 } }).then((r) => r.data),
  usageByService: (days?: number) =>
    api.get<AdminUsageByService>("/admin/usage/by-service", { params: { days: days || 30 } }).then((r) => r.data),
  usageRecent: (limit?: number) =>
    api.get<AdminUsageRecent>("/admin/usage/recent", { params: { limit: limit || 50 } }).then((r) => r.data),
  status: () =>
    api.get<AdminSystemStatus>("/admin/status").then((r) => r.data),
};

// ── User Videos ──

export const userVideosApi = {
  list: (params?: { status?: string }) => api.get("/user-videos").then(r => r.data),
  upload: (file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return api.post("/user-videos/upload", form, { headers: { "Content-Type": "multipart/form-data" } }).then(r => r.data);
  },
  delete: (id: string) => api.delete("/user-videos/" + id).then(r => r.data),
};

export const userPhotosApi = {
  list: () => api.get("/user-photos").then(r => r.data),
  upload: (file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return api.post("/user-photos/upload", form, { headers: { "Content-Type": "multipart/form-data" } }).then(r => r.data);
  },
  delete: (id: string) => api.delete("/user-photos/" + id).then(r => r.data),
};

// Delete wrappers for AI-generated media surfaced in the unified media browser.
export const generatedVideosApi = {
  delete: (id: string) => api.delete("/videos/generated/" + id).then(r => r.data),
};

export const generatedPhotosApi = {
  delete: (id: string) => api.delete("/photos/generated/" + id).then(r => r.data),
};

export const avatarLooksApi = {
  list: (avatarId: string) =>
    api.get(`/avatar/${avatarId}/looks`).then((r) => r.data),
  create: (avatarId: string, payload: { name: string; background_prompt?: string; look_type?: string; pose_angle?: string; product_id?: string; environment?: string; mic_visible?: boolean }) =>
    api.post(`/avatar/${avatarId}/looks`, payload).then((r) => r.data),
  delete: (avatarId: string, lookId: string) =>
    api.delete(`/avatar/${avatarId}/looks/${lookId}`).then((r) => r.data),
  setDefault: (avatarId: string, lookId: string) =>
    api.post(`/avatar/${avatarId}/looks/${lookId}/set-default`).then((r) => r.data),
  generateAllBodyMotion: (avatarId: string) =>
    api.post(`/avatar/${avatarId}/generate-all-body-motion`).then((r) => r.data),
};

export const voiceCorpusApi = {
  list: (avatarId: string) =>
    api.get(`/avatar/${avatarId}/voice-corpus`).then((r) => r.data),
  upload: (avatarId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post(`/avatar/${avatarId}/voice-corpus/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  uploadUrl: (avatarId: string, sourceUrl: string) => {
    const form = new FormData();
    form.append("source_url", sourceUrl);
    return api.post(`/avatar/${avatarId}/voice-corpus/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  delete: (avatarId: string, entryId: string) =>
    api.delete(`/avatar/${avatarId}/voice-corpus/${entryId}`).then((r) => r.data),
  trainFromCorpus: (avatarId: string, corpusEntryIds: string[]) =>
    api.post<{ voice_id: string; voice_name: string }>(
      `/avatar/ai/${avatarId}/clone-voice-from-corpus`,
      { corpus_entry_ids: corpusEntryIds },
    ).then((r) => r.data),
};
export const liveSessionApi = {
  create: (data: any) => api.post("/live-sessions", data).then((r) => r.data),
  list: (params?: { status?: string }) => api.get("/live-sessions").then((r) => r.data),
  get: (id: string) => api.get("/live-sessions/" + id).then((r) => r.data),
  update: (id: string, data: any) => api.put("/live-sessions/" + id, data).then((r) => r.data),
  start: (id: string) => api.post("/live-sessions/" + id + "/start").then((r) => r.data),
  pause: (id: string) => api.post("/live-sessions/" + id + "/pause").then((r) => r.data),
  resume: (id: string) => api.post("/live-sessions/" + id + "/resume").then((r) => r.data),
  stop: (id: string) => api.post("/live-sessions/" + id + "/stop").then((r) => r.data),
  streamUrl: (id: string) => api.get("/live-sessions/" + id + "/stream-url").then((r) => r.data),
  delete: (id: string) => api.delete("/live-sessions/" + id).then((r) => r.data),
};

// ── Go Live (multi-cast / multi-platform / monitor) ──
//
// Sits alongside the legacy `liveSessionsApi` (voice-only MVP). The Go-Live
// endpoints share the /api/live-sessions prefix on the backend.
export const goLiveApi = {
  create: (data: {
    avatar_id: string;
    title?: string;
    cast_selections: Array<{ cast_id: string; render_id?: string; rotation_order?: number }>;
    platforms: Array<{ platform: string; stream_key: string; rtmp_url?: string; enabled?: boolean }>;
    duration_minutes?: number | null;
    background_music_id?: string | null;
    chat_reactivity?: "high" | "medium" | "low";
    product_rotation_minutes?: number;
  }) =>
    api.post<{
      session_id: string;
      status: string;
      relay_stream_key: string;
      session_token: string;
      rtmp_publish_url: string;
      rtmp_play_url: string;
      hls_play_url: string;
      obs_browser_source_url: string;
      monitor_url: string;
    }>("/live-sessions/golive", data).then((r) => r.data),
  status: (sessionId: string) =>
    api.get<Record<string, any>>(`/live-sessions/${sessionId}/golive-status`).then((r) => r.data),
  start: (sessionId: string) =>
    api.post<{ ok: boolean; status: string }>(`/live-sessions/${sessionId}/golive-start`).then((r) => r.data),
  stop: (sessionId: string) =>
    api.post<{ ok: boolean; status: string }>(`/live-sessions/${sessionId}/golive-stop`).then((r) => r.data),
  invite: (sessionId: string, data: { email: string; role?: "admin" | "monitor" | "moderator" }) =>
    api.post<{ invite_id: string; invite_link: string; role: string }>(
      `/live-sessions/${sessionId}/invite`, data
    ).then((r) => r.data),
  invites: (sessionId: string) =>
    api.get<Array<{ id: string; email: string; role: string; accepted: boolean; invited_at: string }>>(
      `/live-sessions/${sessionId}/invites`
    ).then((r) => r.data),
  override: (
    sessionId: string,
    data: {
      action:
        | "skip_product" | "inject_message" | "pause_reactions" | "resume_reactions"
        | "end_stream" | "mute_music" | "unmute_music";
      text?: string;
    },
  ) => api.post(`/live-sessions/${sessionId}/override`, data).then((r) => r.data),
  events: (sessionId: string, limit = 100) =>
    api.get<Array<{ id: string; type: string; data: any; created_at: string }>>(
      `/live-sessions/${sessionId}/events`, { params: { limit } }
    ).then((r) => r.data),
};

// ── Social media (Zernio) ──
//
// Powers the Publish, Published, and Comments pages. All write actions
// require a configured ZERNIO_API_KEY; missing key returns 503 from the
// backend so the UI can show a "Connect your account" prompt.
export const socialApi = {
  listProfiles: () =>
    // Zernio's GET /accounts shape (see docs.zernio.com/api/openapi):
    // {_id, platform, username, displayName, profileUrl, isActive, ...} —
    // NOT {id, label, handle}, which never matched anything real and made
    // every connected account look unconnected on the frontend.
    api.get<Array<{ _id: string; platform: string; username?: string; displayName?: string; isActive?: boolean }>>(
      "/social/profiles"
    ).then((r) => r.data),
  connectPlatform: (platform: string, redirectUri?: string) =>
    api.post<{ auth_url: string; platform: string }>(
      "/social/connect", { platform, redirect_uri: redirectUri },
    ).then((r) => r.data),
  // The OAuth redirect only ever carries a generic error code
  // ("connection_failed") — this looks up Zernio's activity log for the
  // real reason (e.g. "no YouTube channel on this Google account").
  // Best-effort: detail is null when nothing recent is found.
  getConnectError: (platform: string) =>
    api.get<{ detail: string | null }>(
      "/social/connect-error", { params: { platform } },
    ).then((r) => r.data),
  generateCaption: (data: { cast_id: string; platform: string }) =>
    api.post<{ caption: string; hashtags: string[]; first_comment: string | null }>(
      "/social/generate-caption", data
    ).then((r) => r.data),
  createPost: (data: {
    cast_id: string;
    render_id?: string;
    caption: string;
    hashtags: string[];
    first_comment?: string | null;
    platforms: Array<{ platform: string; accountId?: string; scheduled_for?: string }>;
    scheduled_for?: string | null;
    publish_now?: boolean;
  }) => api.post<Record<string, any>>("/social/posts", data).then((r) => r.data),
  listPosts: (opts?: { castId?: string; status?: string }) =>
    api.get<Array<Record<string, any>>>("/social/posts", {
      params: { cast_id: opts?.castId, status: opts?.status },
    }).then((r) => r.data),
  getPost: (postId: string) =>
    api.get<Record<string, any>>(`/social/posts/${postId}`).then((r) => r.data),
  getPendingCommentCount: () =>
    api.get<{ count: number }>("/social/comments/pending-count").then((r) => r.data),
  deletePost: (postId: string) =>
    api.delete(`/social/posts/${postId}`).then((r) => r.data),
  getComments: (postId: string) =>
    api.get<Array<Record<string, any>>>(`/social/posts/${postId}/comments`).then((r) => r.data),
  reply: (postId: string, commentId: string, text?: string) =>
    api.post(`/social/posts/${postId}/comments/${commentId}/reply`, { text }).then((r) => r.data),
  skip: (postId: string, commentId: string) =>
    api.post(`/social/posts/${postId}/comments/${commentId}/skip`).then((r) => r.data),

  // ── Channels (Publish hub) ───────────────────────────────────────────
  listChannels: () =>
    api.get<{ channels: SocialChannel[] }>("/social/channels").then((r) => r.data.channels),
  disconnectChannel: (channelId: string) =>
    api.delete<{ ok: boolean }>(`/social/channels/${channelId}`).then((r) => r.data),
  checkAvatarConsistency: (channelId: string, avatarId: string) =>
    api.get<AvatarConsistencyResponse>(
      `/social/channels/${channelId}/avatar-check`, { params: { avatar_id: avatarId } },
    ).then((r) => r.data),
};

export interface SocialChannel {
  id: string;
  user_id: string;
  platform: string;
  platform_account_id?: string | null;
  handle?: string | null;
  display_name?: string | null;
  follower_count: number;
  profile_image_url?: string | null;
  zernio_account_id?: string | null;
  primary_avatar_id?: string | null;
  primary_avatar?: {
    id: string;
    name?: string;
    face_image_url?: string | null;
    face_ref_key?: string | null;
  } | null;
  avatar_history: Array<{
    avatar_id: string;
    avatar_name?: string;
    post_count: number;
    first_used?: string;
    last_used?: string;
  }>;
  total_posts: number;
  total_scheduled: number;
  status: "active" | "disconnected" | "token_expired";
  connected_at?: string | null;
  last_seen_at?: string | null;
}

export interface AvatarConsistencyResponse {
  status: "match" | "new_channel" | "mismatch";
  primary_avatar_id?: string | null;
  primary_avatar?: { id: string; name?: string } | null;
  channel_total_posts?: number;
}

// ── User action history (audit log) ──

export interface HistoryEvent {
  id: string;
  user_id: string | null;
  session_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  cast_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

export interface HistoryResponse {
  events: HistoryEvent[];
  next_cursor: string | null;
}

export const historyApi = {
  forCast: (castId: string, params?: { limit?: number; before?: string | null }) =>
    api.get<HistoryResponse>(`/casts/${castId}/history`, {
      params: { limit: params?.limit ?? 50, before: params?.before ?? undefined },
    }).then((r) => r.data),
  forMe: (params?: { limit?: number; before?: string | null; action?: string; entity_type?: string }) =>
    api.get<HistoryResponse>(`/users/me/history`, {
      params: {
        limit: params?.limit ?? 100,
        before: params?.before ?? undefined,
        action: params?.action ?? undefined,
        entity_type: params?.entity_type ?? undefined,
      },
    }).then((r) => r.data),
  forAdmin: (params?: { limit?: number; before?: string | null; user_id?: string; cast_id?: string; action?: string; entity_type?: string; since?: string; until?: string }) =>
    api.get<HistoryResponse>(`/admin/history`, { params }).then((r) => r.data),
};

// ── Stock Media (Pexels) ──

export const stockMediaApi = {
  searchPhotos: (params: { q: string; orientation?: string; page?: number; per_page?: number }) =>
    api.get("/stock-media/photos", { params }).then((r) => r.data),
  searchVideos: (params: { q: string; orientation?: string; min_duration?: number; max_duration?: number; page?: number; per_page?: number }) =>
    api.get("/stock-media/videos", { params }).then((r) => r.data),
  importMedia: (data: { url: string; type: string; pexels_id: number; name?: string }) =>
    api.post("/stock-media/import", data).then((r) => r.data),
};
