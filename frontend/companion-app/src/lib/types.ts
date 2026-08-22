// ── Enums ──

// Matches backend UserRole (models/user.py) exactly — uppercase on both
// sides. Unlike TeamRole below, this is the account-level role and its
// values are compared directly against the API's `user.role` string.
export enum UserRole {
  CREATOR = "CREATOR",
  OPERATOR = "OPERATOR",
  ADMIN = "ADMIN",
}

// Per-workspace Teams role — distinct from UserRole (account-holder /
// global admin). Matches backend TeamRole (models/user.py) exactly,
// lowercase on both sides.
export enum TeamRole {
  VIEWER = "viewer",
  CREATOR = "creator",
  PUBLISHER = "publisher",
}

export enum CastStatus {
  DRAFT = "DRAFT",
  OUTLINE_REVIEW = "OUTLINE_REVIEW",
  SCRIPT_REVIEW = "SCRIPT_REVIEW",
  TEMPLATE_SELECT = "TEMPLATE_SELECT",
  PENDING_PAYMENT = "PENDING_PAYMENT",
  GENERATING_TTS = "generating_tts",
  TTS_READY = "tts_ready",
  GENERATING_VIDEOS = "generating_videos",
  GENERATING = "GENERATING",
  GENERATION_FAILED = "GENERATION_FAILED",
  READY = "READY",
  SCHEDULED = "SCHEDULED",
  LIVE = "LIVE",
  COMPLETED = "COMPLETED",
}

export enum BlockType {
  INTRO = "INTRO",
  HOOK = "HOOK",
  PRODUCT = "PRODUCT",
  PRODUCT_DEMO = "PRODUCT_DEMO",
  TESTIMONIAL = "TESTIMONIAL",
  FEATURE_SHOWCASE = "FEATURE_SHOWCASE",
  COMPARISON = "COMPARISON",
  SOCIAL_PROOF = "SOCIAL_PROOF",
  FLASH_SALE = "FLASH_SALE",
  URGENCY = "URGENCY",
  QA = "QA",
  EDUCATIONAL = "EDUCATIONAL",
  STORY = "STORY",
  CTA = "CTA",
  CLOSING = "CLOSING",
  TRANSITION = "TRANSITION",
  FILLER = "FILLER",
  IDLE = "IDLE",
}

export enum BlockCategory {
  AVATAR_SPEAKING = "avatar_speaking",
  AVATAR_BODY_MOTION = "avatar_body_motion",
  VOICEOVER_VIDEO = "voiceover_video",
  VOICEOVER_STOCK_VIDEO = "voiceover_stock_video",
  VOICEOVER_PHOTO = "voiceover_photo",
  VOICEOVER_STOCK_PHOTO = "voiceover_stock_photo",
  PIP = "pip",
  MUSIC_ONLY = "music_only",
  PRODUCT_SHOWCASE = "product_showcase",
}

export enum LayoutMode {
  FULL_AVATAR = "full_avatar",
  AVATAR_PRODUCT = "avatar_product",
  SPLIT_SCREEN = "split_screen",
  PIP_PRODUCT = "pip_product",
  PRODUCT_ONLY = "product_only",
  AUTO_BASKET = "auto_basket",
}

export enum AvatarType {
  CLONE = "CLONE",
  DIGITAL = "DIGITAL",
}

export enum AvatarStatus {
  DRAFT = "DRAFT",
  PROCESSING = "PROCESSING",
  CANDIDATES_READY = "CANDIDATES_READY",
  FACE_CANDIDATES_READY = "FACE_CANDIDATES_READY",
  READY = "READY",
  APPROVED = "APPROVED",
  FAILED = "FAILED",
}

export enum StreamStatus {
  LIVE = "live",
  PAUSED = "paused",
  IDLE = "idle",
  STOPPED = "stopped",
}

// ── Auth ──

export interface User {
  id: string;
  email: string;
  role: UserRole;
  tiktok_handle?: string;
  stripe_customer_id?: string;
  created_at: string;
  is_active: boolean;
  display_name?: string | null;
  avatar_url?: string | null;
  // Affiliate IDs — empty/null means the user hasn't connected the
  // platform. `tiktok_affiliate_id` is the TikTok Shop affiliate ID;
  // `amazon_associate_tag` is the Amazon Associates tracking tag.
  tiktok_affiliate_id?: string | null;
  amazon_associate_tag?: string | null;
  // Which workspace the current session's token is acting in — resolved
  // server-side from the JWT's wsid claim on every /auth/me call. Cosmetic
  // only on the frontend; the backend is the real enforcement point.
  workspace?: WorkspaceInfo;
}

export interface WorkspaceInfo {
  owner_id: string;
  owner_label: string;
  role?: TeamRole | null; // null when is_own is true
  is_own: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginRequest {
  email: string;
  password: string;
  remember_me?: boolean;
}

export interface RegisterRequest {
  email: string;
  password: string;
  tiktok_handle?: string;
}

export interface ForgotPasswordRequest {
  email: string;
}

export interface ResetPasswordRequest {
  token: string;
  password: string;
}

export interface MessageResponse {
  message: string;
}

// ── Products ──

export interface Product {
  id: string;
  name: string;
  price: number;
  commission_rate: number | null;
  // Commission attribution — added in PR #23. `manual` covers Shopify
  // and any generic source where the user enters the rate by hand.
  commission_source?: "tiktok_affiliate" | "amazon_associates" | "manual" | null;
  commission_category?: string | null;
  affiliate_tag?: string | null;
  description?: string;
  full_description?: string;
  variants?: ProductVariant[];
  specifications?: ProductSpec[];
  selling_points?: string[];
  shop_rating?: number;
  shop_followers?: number;
  shop_identity_label?: string;
  store_sub_scores?: Record<string, { score?: number; percentage: string }>;
  sold_last_30_days?: number;
  tiktok_product_url?: string;
  media_keys?: string[];
  overlay_key?: string;
  cover_image_key?: string;
  cover_image_url?: string;
  created_at: string;
}

export interface ProductCreate {
  name: string;
  price: number;
  commission_rate?: number;
  description?: string;
  tiktok_product_url?: string;
  media_keys?: string[];
}

// ── Blocks ──

export interface Variant {
  id: string;
  variant_label: string;
  script_text?: string;
  status: string;
  audio_key?: string;
  video_key?: string;
  final_video_key?: string;
  clip_url?: string;
  stream_url?: string;
  composition_warnings?: Array<{ step: string; error: string }>;
  audio_url?: string;
  motion_prompt?: string;
  tts_duration_seconds?: number;
  duration_seconds?: number;
  is_active?: boolean;
  generation_error?: string;
  caption_words?: Array<{ word: string; start: number; end: number; probability: number }>;
  caption_segments?: Array<{ start: number; end: number; text: string }>;
}

export type RenderMode = "avatar_full" | "voiceover" | "pip" | "body_motion" | "motion";
export type PipEngine = "infinitetalk_rendered" | "musetalk_live";

export interface Block {
  id: string;
  type: BlockType;
  category?: BlockCategory | string;
  position: number;
  product_id?: string;
  layout_mode: LayoutMode;
  render_mode?: RenderMode;
  user_video_asset_id?: string;
  avatar_look_id?: string;
  pip_engine?: PipEngine;
  scene_image_key?: string;
  auto_basket_enabled: boolean;
  auto_basket_timeout: number;
  mood?: string;
  key_points?: string[];
  variant_count: number;
  variants?: Variant[];
  body_motion_start_look_id?: string;
  body_motion_end_look_id?: string;
  body_motion_prompt?: string;
  /** PR #66 Fix 2: hydrated start/end frame image URLs returned by
   * GET /api/casts/{id} so block #2+ render their thumbnails on mount
   * without waiting for an avatar-looks refetch. Null when no frame
   * is pinned yet. */
  first_frame_url?: string | null;
  last_frame_url?: string | null;
  /** PR #66 Fix 1: server-resolved default background. Order of
   * preference: explicit stock_media_url → parallel_media[0] → avatar
   * profile face image. Editor renders this when no explicit
   * background is set instead of the empty-state arrow placeholder. */
  default_background_url?: string | null;
  /** Per-block frame seed prompts authored by the script-writer. The
   * editor's start/end frame carousel pre-fills its "+ Add Frame" modal
   * from these so the user can author / regenerate frames without
   * starting from a blank textarea. */
  body_motion_start_prompt?: string | null;
  body_motion_end_prompt?: string | null;
  /** avatar_action: per-block scene frame prompts (mirror of
   * body_motion_*_prompt but seeded with FLUX-generated scene frames
   * instead of generic body shots). Replaces the legacy avatar_acting
   * frame fields at the editor boundary. */
  action_start_prompt?: string | null;
  action_end_prompt?: string | null;
  /** avatar_action / generated_video motion prompt — alias for
   * body_motion_prompt at the API boundary so the frontend can read a
   * single field regardless of category. */
  motion_prompt?: string;
  avatar_angle?: string;
  /** Parallel-media items shown ON TOP of voiceover/speaking blocks. */
  parallel_media?: ParallelMediaItem[];
  // Smart Cast metadata returned by /casts/{id}. Optional for legacy
  // blocks that pre-date the Smart Cast outline pipeline.
  hook_type?: string | null;
  background_type?: string | null;
  transition_in?: string | null;
  energy_level?: string | null;
  /** AI-suggested Pexels query — shown as a chip in the b-roll picker
      and used as the default search seed. */
  stock_media_query?: string | null;
  stock_media_url?: string | null;
  stock_media_thumbnail?: string | null;
  stock_media_kind?: "video" | "photo" | null;
  stock_media_pexels_id?: string | null;
  /** Generic per-block metadata bag — backed by a JSONB column. Carousel
      keys live here today; future per-block flags share the same field. */
  metadata?: BlockMetadata;
  /** PR #76: per-block voiceover toggle for action blocks. null/undefined
      = LLM default (treated as enabled when dialogue is present); true =
      voiceover plays over the motion clip; false = silent action, dialogue
      is dropped at render. Surfaced from block_metadata.voiceover_enabled
      by the backend for convenience. */
  voiceover_enabled?: boolean | null;
}

/** PIP layout choices for talking-head blocks (PR #83). Decides whether
    the avatar fills the canvas or rides as a small top-corner overlay.
    Default for product-reveal / feature blocks is `pip_small`. */
export type PipLayout = "fullscreen" | "pip_small" | "pip_medium" | "hidden";

/** Per-block ad-hoc metadata. All keys optional; absence == feature off. */
export interface BlockMetadata {
  /** Toggle the product carousel for this block (multi-asset fade-through). */
  product_carousel?: boolean;
  /** Seconds each carousel item is fully visible. Range 1.5-6 (validated server-side). */
  carousel_speed_seconds?: number;
  /** Transition between carousel items. */
  carousel_transition?: "crossfade" | "slide" | "zoom";
  /** Ordered subset of product asset IDs to cycle through. If absent, all
      assets in their default order are used. */
  carousel_asset_ids?: string[];
  /** Talking-head PIP window layout. `fullscreen` keeps legacy behaviour
      (avatar fills the 1080×1920 canvas). `pip_small` (default for product
      / feature / explanation blocks) renders the avatar as a 346×346
      rounded-square in the top-left at 18% of canvas height. `pip_medium`
      is the same shape at 25% (480×480). `hidden` plays the audio without
      a visible face. */
  pip_layout?: PipLayout;
  [key: string]: unknown;
}

export interface ParallelMediaItem {
  kind: "video" | "photo";
  url: string;
  thumbnail?: string;
  pexels_id?: string | null;
  source?: "pexels" | "upload";
  start_offset_s: number;
  duration_s?: number | null;
  /** Set when the Smart Cast pipeline picked this item automatically.
      The picker badges these so the user knows it's an AI suggestion
      they can keep or swap. */
  ai_suggested?: boolean;
}

export interface BlockCreate {
  type: BlockType;
  position: number;
  product_id?: string;
  layout_mode?: LayoutMode;
  scene_image_key?: string;
  auto_basket_enabled?: boolean;
  auto_basket_timeout?: number;
  mood?: string;
  key_points?: string[];
  chat_rules?: Record<string, unknown>;
}

// ── Casts ──

export interface BackgroundConfig {
  type?: "original" | "color" | "image" | "video" | "blur-sm" | "gradient";
  color?: string;
  image_key?: string;
  video_key?: string;
  blur_strength?: number;
  gradient?: {
    from?: string;
    to?: string;
    direction?: "vertical" | "horizontal" | "diagonal";
  };
  animation?: "none" | "ken_burns" | "slow_zoom";
  ken_burns_speed?: number;
}

export interface ScreenOverlay {
  id: string;
  kind: "text" | "sfx" | "sticker";
  block_ids: string[];
  start_ms: number;
  duration_ms: number;
  x: number;
  y: number;
  text?: string;
  font?: "default" | "impact" | "handwritten" | "pixel" | "serif";
  font_size_pct?: number;
  color?: string;
  bg?: string;
  stroke?: string;
  animation?: "none" | "shake" | "bounce" | "pulse" | "slide_in_left" | "slide_in_right" | "slide_in_top" | "slide_in_bottom" | "typewriter" | "fade_in" | "zoom_in";
  sfx_preset?: "ping" | "swoosh" | "bell" | "cash_register" | "drum_roll" | "chime";
  sfx_volume?: number;
  sticker_emoji?: string;
  sticker_size_pct?: number;
}

export interface SceneObject {
  id: string;
  kind: "avatar" | "product" | "text" | "sticker" | "sfx" | "image";
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
  opacity: number;
  z_index: number;
  block_ids: string[];
  start_ms: number;
  duration_ms: number;
  animation_in?: string;
  animation_out?: string;
  animation_loop?: string;
  avatar?: { fit: "cover" | "contain"; bg_fill: "blur-sm" | "black" | "transparent" };
  product?: { product_id: string; show_price: boolean; show_title: boolean };
  text?: { content: string; style: string };
  sticker?: { emoji: string };
  sfx?: { preset: string; volume: number };
  image?: { r2_key: string; mask_bg?: boolean; filter?: string };
}

export interface EffectsConfig {
  background?: BackgroundConfig;
  product_overlay?: {
    enabled?: boolean;
    layout?: string;
    size?: number;
    animation?: string;
    show_price?: boolean;
    show_title?: boolean;
  };
  floating_reactions?: {
    enabled?: boolean;
    emojis?: string[];
  };
  confetti?: {
    enabled?: boolean;
  };
  lower_third?: {
    enabled?: boolean;
  };
  pip_avatar?: {
    enabled?: boolean;
  };
  branding?: {
    enabled?: boolean;
    text?: string;
  };
  avatar_fit?: {
    mode?: "cover" | "contain" | "custom_crop";
    crop?: { x: number; y: number; scale: number };
    bg_fill?: "black" | "blur-sm" | "transparent";
  };
  overlays?: ScreenOverlay[];
  scene_objects?: SceneObject[];
}

export interface Cast {
  id: string;
  name?: string;
  status: CastStatus;
  avatar_id: string;
  template_name?: string;
  max_duration_minutes: number;
  loop: boolean;
  creation_fee_cents?: number;
  creation_paid: boolean;
  generation_progress: number;
  generation_error?: string;
  quality?: string;
  progress_step?: string;
  total_clips?: number;
  completed_clips?: number;
  render_status?: string;
  render_error_message?: string | null;
  effects_config?: EffectsConfig;
  output_format?: string;
  script_direction?: string;
  description?: string;
  duration_target_seconds?: number | null;
  platform_target?: string;
  /** Production level — quick | standard | premium */
  production_level?: string;
  // Phase 2.4 — Cross-format cast support
  format_family?: string;
  parent_cast_id?: string | null;
  target_platforms?: string[];
  // Phase 2.5 — Linked cascading update
  audio_stale_since?: string | null;
  // Cast versioning
  version?: number;
  // Background music + captions (added in Mubert + Captions feature)
  background_music_url?: string | null;
  background_music_mood?: string | null;
  background_music_tags?: string[] | null;
  /** "off" | "auto" | "track_id:<id>" */
  music_track_choice?: string;
  music_volume?: number | null;
  caption_preset?: Record<string, unknown> | null;
  default_avatar_look_id?: string | null;
  cast_type?: "recorded" | "live";
  template_id?: string | null;
  created_at: string;
  blocks?: Block[];
  products?: Product[];
}

export interface CastCreate {
  name?: string;
  avatar_id: string;
  template_name?: string;
  /** Stage-1 creative template id (see castsApi.templates). Omit / undefined
   *  means "Auto / let AI choose". */
  template_id?: string;
  max_duration_minutes?: number;
  loop?: boolean;
  quality?: string;
  output_format?: string;
  script_direction?: string;
  description?: string;
  duration_target_seconds?: number | null;
  platform_target?: string;
  /** Production level — quick | standard | premium */
  production_level?: string;
  /** "off" | "auto" | "custom" | "track_id:<id>" (legacy) */
  music_track_choice?: string;
  /** Set together with music_track_choice="custom" — a real library track
   *  picked via MusicTrackPickerModal. */
  background_music_url?: string;
  background_music_mood?: string;
  background_music_tags?: string[];
  products?: ProductCreate[];
  blocks?: BlockCreate[];
}

/** Stage-1 creative template summary returned by GET /api/casts/templates. */
export interface CastTemplate {
  id: string;
  name: string;
  description: string;
  preview_image_key: string | null;
  block_count: number;
  est_duration_range: [number, number];
  default_bias: {
    avatar_speaking: number;
    broll: number;
    uploaded_video: number;
  };
  default_mic_on: boolean;
  default_caption_preset: string;
}

export interface CastListResponse {
  casts: Cast[];
  total: number;
}

export interface OutlineScene {
  block_type: string;
  mood: string;
  key_points: string[];
  style_directives: string[];
  estimated_duration_seconds: number;
  product_name?: string;
}

export interface OutlineResponse {
  cast_id: string;
  scenes: OutlineScene[];
  estimated_total_duration_seconds: number;
}

export interface GenerationStatus {
  cast_id: string;
  status: string;
  progress: number;
  current_step?: string;
  failed_count: number;
  total_variants: number;
}

// ── Avatar ──

export interface StyleDNAResult {
  tone?: string;
  avg_energy?: number;
  avg_sentence_length?: number;
  common_phrases?: string[];
  sentence_starters?: string[];
  sign_offs?: string[];
  question_frequency?: number;
  hook_pattern?: string;
  cut_frequency_seconds?: number;
  broll_ratio?: number;
  caption_preset?: string;
  preferred_transitions?: string[];
  music_energy?: string;
  music_genre?: string;
  voice_model_id?: string | null;
  voice_duration_s?: number;
  source_urls?: string[];
  analyzed_at?: string;
}

export interface Avatar {
  id: string;
  type: AvatarType;
  status: AvatarStatus;
  name?: string;
  description?: string;
  voice_style?: string;
  background?: string;
  camera_position?: string;
  style?: string;
  ai_model?: string;
  appearance_prompt?: string;
  face_ref_key?: string;
  voice_id?: string;
  test_script?: string;
  test_audio_key?: string;
  test_video_key?: string;
  test_video_url?: string;
  face_image_url?: string;
  persona_profile?: Record<string, unknown>;
  style_dna?: StyleDNAResult | null;
  candidate_frames?: string[];
  tiktok_source_url?: string;
  progress_step?: string;
  progress_percent?: number;
  regeneration_count?: number;
  voice_corpus_count?: number;
  target_audience?: Record<string, unknown>;
  body_description?: string;
  locked_test_script?: string;
  preview_video_url?: string;
  gender?: string;
  style_preset?: string;
  imperfections?: string[];
  render_status?: {
    state?: string;
    position?: number;
    eta?: number;
    confidence?: "high" | "low" | "none";
    error_message?: string;
    elapsed_seconds?: number;
  };
  wizard_step?: string;
  detected_language?: string;
  /** PR #65: clip-on lavalier vs phone-mic toggle. Default false = phone mic.
   * Drives the TTS post-process EQ profile AND the mic-style suffix
   * appended to the voice description sent to the cloning engine. */
  clip_mic_enabled?: boolean;
  created_at?: string;
}

// ── Stream ──

export interface StreamState {
  session_id: string;
  cast_id: string;
  status: StreamStatus;
  uptime_seconds: number;
  viewers: number;
  total_purchases: number;
  total_gmv: number;
  current_block_position?: number;
  current_block_type?: string;
  streaming_cost_cents: number;
}

export interface StreamStartRequest {
  cast_id: string;
  rtmp_url: string;
  mode?: "sequential" | "shuffle";
}

// ── Chat ──

export interface ChatMessage {
  id: string;
  session_id: string;
  viewer_username: string;
  message_text: string;
  is_purchase: boolean;
  ai_draft?: string;
  ai_draft_status: string;
  responded_by?: string;
  response_text?: string;
  locked_by?: string;
  timestamp: string;
}

// ── Analytics ──

export interface SessionSummary {
  id: string;
  cast_id: string;
  started_at: string;
  ended_at?: string;
  duration_minutes: number;
  peak_viewers: number;
  total_purchases: number;
  total_gmv: number;
  streaming_cost_cents: number;
  afk_events: number;
}

export interface VariantPerformance {
  variant_id: string;
  block_id: string;
  block_type: string;
  product_name?: string;
  times_played: number;
  purchases_during: number;
  performance_score?: number;
  script_preview?: string;
}

// ── Admin ──

export interface AdminDashboard {
  active_streams: number;
  total_creators: number;
  revenue_today_cents: number;
  server_health: {
    status: string;
    cpu_percent: number;
    memory_percent: number;
    disk_percent: number;
  };
}

export interface AdminStream {
  session_id: string;
  cast_id: string;
  creator_email: string;
  status: string;
  uptime_seconds: number;
  viewers: number;
}

export interface AdminCreator {
  id: string;
  email: string;
  /** Real, OAuth-verified TikTok connections (via Zernio) — a user can have
   *  more than one connected. Not the same as the optional free-text handle
   *  collected at signup. */
  tiktok_accounts: { handle: string | null; follower_count: number }[];
  total_casts: number;
  total_streams: number;
  total_revenue_cents: number;
  created_at: string;
  is_active: boolean;
}

// ── Control Panel Types ──

export interface AdminPrompt {
  key: string;
  name: string;
  description: string;
  category: string;
  system_prompt: string;
  character_count: number;
  used_in: string;
  version: number;
  last_updated: string | null;
}

export interface AdminPromptVersion {
  id: string;
  version: number;
  prompt_text: string;
  system_prompt: string;
  changed_by: string;
  change_reason: string;
  created_at: string;
}

export interface AdminPromptDetail extends AdminPrompt {
  history: AdminPromptVersion[];
}

export interface AdminUsageDailyEntry {
  date: string;
  services: Record<string, { calls: number; cost_cents: number; success: number; fail: number }>;
  total_calls: number;
  total_cost_cents: number;
  success_count: number;
  fail_count: number;
}

export interface AdminUsageDaily {
  daily: AdminUsageDailyEntry[];
  days: number;
}

export interface AdminUsageUserEntry {
  user_id: string;
  email: string;
  total_calls: number;
  total_cost_cents: number;
  success_rate: number;
}

export interface AdminUsageByUser {
  users: AdminUsageUserEntry[];
  days: number;
}

export interface AdminUsageServiceEntry {
  service: string;
  total_calls: number;
  total_cost_cents: number;
  avg_duration_seconds: number;
  success_count: number;
  fail_count: number;
  success_rate: number;
}

export interface AdminUsageByService {
  services: AdminUsageServiceEntry[];
  days: number;
}

export interface AdminUsageLogEntry {
  id: string;
  user_id: string;
  service: string;
  operation: string;
  duration_seconds: number | null;
  cost_cents: number;
  success: boolean;
  error_message: string | null;
  runpod_job_id: string | null;
  created_at: string;
}

export interface AdminUsageRecent {
  logs: AdminUsageLogEntry[];
  total: number;
}

export interface AdminSystemStatus {
  server: {
    status: string;
    cpu_percent: number;
    memory_percent: number;
    memory_used_gb: number;
    memory_total_gb: number;
    disk_percent: number;
    disk_used_gb: number;
    disk_total_gb: number;
    uptime_seconds: number;
  };
  gpu_server: {
    status: string;
    url: string;
  };
  runpod_endpoints: Record<string, string>;
  r2_storage: {
    configured: boolean;
    bucket: string;
    public_url: string;
  };
  database: Record<string, number>;
  sentry_configured: boolean;
  zernio: {
    configured: boolean;
    failures_24h: number;
    last_error: string | null;
  };
}

// ── WebSocket Events ──

export type WSServerEvent =
  | { type: "STREAM_STATE_UPDATE"; payload: { status: StreamStatus; uptime_seconds: number; viewers: number; total_purchases: number; total_gmv: number } }
  | { type: "BLOCK_TRANSITION"; payload: { current_block_id: string; current_block_position: number; total_blocks: number; block_type: BlockType; layout_mode: LayoutMode; product_to_pin: { product_id: string; name: string; price: number } | null; afk_timer_seconds: number | null; next_block_preview: { block_type: string; product_name: string | null } } }
  | { type: "NEW_CHAT_MESSAGE"; payload: { message_id: string; viewer_username: string; message_text: string; is_purchase: boolean; ai_draft: string | null; ai_draft_status: string | null } }
  | { type: "CHAT_DRAFT_LOCKED"; payload: { message_id: string; locked_by: string } }
  | { type: "AFK_TIMER_TICK"; payload: { seconds_remaining: number; product_id: string; product_name: string } }
  | { type: "AFK_TRIGGERED"; payload: { product_id: string; overlay_active: boolean; chat_cta_sent: boolean } }
  | { type: "OPERATOR_PRESENCE"; payload: { operators: Array<{ user_id: string; name: string; status: "online" | "idle" }> } }
  | { type: "GENERATION_PROGRESS"; payload: { cast_id: string; progress: number; current_step: string; failed_count: number } };

export type WSClientEvent =
  | { type: "PIN_CONFIRM"; payload: { product_id: string; operator_name: string } }
  | { type: "APPROVE_CHAT"; payload: { message_id: string; edited_text: string | null } }
  | { type: "REJECT_CHAT"; payload: { message_id: string } }
  | { type: "LOCK_CHAT_DRAFT"; payload: { message_id: string } }
  | { type: "SEND_CHAT"; payload: { text: string; mode: "chat_only" | "voice_and_chat" } }
  | { type: "STREAM_CONTROL"; payload: { action: "skip" | "previous" | "pause" | "resume" } };

// ── Clone Flow ──

export interface VideoComment {
  user: string;
  text: string;
  likes: number;
}

export interface VideoInfo {
  thumb_url: string;
  video_url: string;
  web_video_url: string;
  duration_seconds: number;
  video_r2_key: string;
  description: string;
  views: number;
  has_face: boolean;
  face_confidence: number;
  likes: number;
  create_time?: string;
  comments?: VideoComment[] | null;
}

export interface FaceCandidate {
  url: string;
  score: number;
}

export interface FetchVideosResponse {
  videos: VideoInfo[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface DownloadVideoResponse {
  video_url: string;
  video_r2_key: string;
  duration_seconds: number;
}

export interface FaceCandidatesResponse {
  candidates: FaceCandidate[];
  voice_clone_progress: number;
  status?: string;
  progress_step?: string;
}

export interface EditFrameResponse {
  original_url: string;
  edited_url: string;
}

export interface UploadFaceResponse {
  face_ref_key: string;
  face_url: string;
}

// ── Product Discovery ──

export interface DiscoverProduct {
  id: string;
  tiktok_product_id: string;
  title: string;
  description: string;
  product_url: string;
  current_price: number;
  original_price: number;
  discount_percent: number;
  revenue_cents: number;
  revenue_display: string;
  revenue_growth_rate: number;
  items_sold: number;
  avg_unit_price: number;
  rating: number;
  review_count: number;
  seller_name: string;
  commission_rate: number;
  commission_display: string;
  is_affiliate: boolean;
  creator_count: number;
  category: string;
  subcategory: string;
  cover_image_url: string;
  cover_image_r2_key: string;
  additional_image_urls: string[];
  section: string;
  rank_position: number;
  region: string;
}

export interface DiscoverResponse {
  products: DiscoverProduct[];
  total: number;
  page: number;
  per_page: number;
  section?: string;
  region?: string;
  source?: string;
}

export interface CategoryTree {
  name: string;
  subcategories: string[];
}

// ── TikTok Shop ──

export interface TikTokShopProduct {
  product_id: string;
  title: string;
  product_url: string;
  current_price: string;
  original_price: string;
  discount_percent: number;
  sales_volume: number;
  rating: number;
  review_count: number;
  seller_name: string;
  image_urls: string[];
  tags: string[];
  search_rank: number;
  category: string;
}

// ── Channels ──

export interface Channel {
  id: string;
  platform: string;
  handle: string;
  display_name: string;
  bio: string;
  profile_image_key: string;
  followers_count: number;
  video_count: number;
  avg_views: number;
  stream_key: string;
  stream_url: string;
  index_status: string;
  indexed_video_count: number;
  index_target_count: number;
  relevant_video_count: number;
  last_indexed_at?: string;
  voice_profile?: VoiceProfile;
  status: string;
  created_at: string;
}

export interface VoiceProfile {
  tone: string;
  status?: string;
  avg_sentence_length?: number;
  common_phrases?: string[];
  filler_words?: string[];
  question_frequency?: number;
  exclamation_frequency?: number;
  vocabulary_level?: string;
  topics?: string[];
  cta_style?: string;
  avg_energy?: string;
  sentence_starters?: string[];
  sign_offs?: string[];
  language?: string;
  sample_phrases?: string[];
  indexed_relevant_videos?: number;
  indexed_total_videos?: number;
  filtered_out_count?: number;
  indexed_count?: number;
}

export interface ChannelTranscript {
  id: string;
  video_url: string;
  video_title: string;
  video_duration_seconds: number;
  video_views: number;
  is_relevant: boolean;
  relevance_reason: string;
  speech_ratio: number;
  has_main_speaker: boolean;
  word_count: number;
  status: string;
}

// ── Product Assets ──

export interface ProductVariant {
  variantId?: string;
  name: string;
  price?: string;
  originalPrice?: string;
  discountPercent?: string;
  stockStatus?: string;
  stockQuantity?: number;
  imageUrl?: string | null;
}

export interface ProductSpec {
  name: string;
  value: string;
}

export interface ProductAsset {
  id: string;
  asset_type: string;
  media_type: string;
  r2_key: string;
  r2_url: string;
  position?: number;
  duration_seconds: number;
  width: number;
  height: number;
  file_size_bytes: number;
  generation_prompt?: string;
  generation_model?: string;
  created_at?: string;
}

export interface ProductWithAssets extends Product {
  tiktok_product_id?: string;
  current_price: number;
  original_price: number;
  discount_percent: number;
  sales_volume: number;
  rating: number;
  review_count: number;
  seller_name: string;
  category: string;
  tags: string[];
  cover_image_key: string;
  cover_image_url?: string;
  product_url?: string;
  status: string;
  asset_count: number;
  video_count: number;
  image_count: number;
  assets?: ProductAsset[];
  assets_status?: {
    has_cover: boolean;
    image_count: number;
    video_count: number;
    has_video: boolean;
  };
  casts?: Array<{
    id: string;
    name: string;
    status: string;
  }>;
}

export interface ProductDetailResponse extends ProductWithAssets {
  cover_image_url: string;
  product_url: string;
  casts: { id: string; name: string; status: string }[];
  assets_status: {
    has_cover: boolean;
    image_count: number;
    video_count: number;
    has_video: boolean;
  };
}

// ── Layout Templates ──

export interface LayoutTemplate {
  id: string;
  user_id?: string;
  name: string;
  config: {
    avatar: { x: number; y: number; width: number; height: number };
    product: { x: number; y: number; width: number; height: number };
    text_overlay: string;
    canvas_width: number;
    canvas_height: number;
  };
  is_preset: boolean;
  created_at?: string;
}

// ── Templates ──

export const CAST_TEMPLATES = [
  { id: "beauty_haul", name: "Beauty Haul", description: "Intro → Product → Social Proof → Product → Flash Sale → Bundle CTA → Closing", icon: "sparkles" },
  { id: "tech_unbox", name: "Tech Unbox", description: "Intro → Unboxing → Specs → Demo → Versus → Verdict CTA → Closing", icon: "cpu" },
  { id: "flash_marathon", name: "Flash Marathon", description: "Intro → Product → Countdown → Product → Countdown → Mega Deal → Closing", icon: "zap" },
  { id: "lifestyle", name: "Lifestyle Show", description: "Intro → Mood → Product in Context → Testimonial → Product → Soft CTA → Closing", icon: "heart" },
  { id: "deep_dive", name: "Single Product Deep Dive", description: "Intro → Story → Problem → Solution → Demo → Reviews → Offer → Closing", icon: "search" },
  { id: "custom", name: "Custom", description: "Drag-and-drop any blocks into your own sequence", icon: "settings" },
] as const;

// ── User Video Assets ──

export interface UserVideoAsset {
  id: string;
  name: string;
  url: string;
  r2_key: string;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  file_size_bytes: number | null;
  original_filename: string;
  created_at: string;
}

export interface AvatarLook {
  id: string;
  avatar_id: string;
  name: string;
  background_prompt: string | null;
  face_ref_key: string | null;
  image_url: string | null;
  is_default: boolean;
  is_original: boolean;
  status: "pending" | "generating" | "ready" | "failed";
  // Historic values: "background", "body_motion", "tryon". Per-block
  // body-motion frames mint per-block keys
  // ("body_motion_block_<block_id>_start" / "_end") so this widened to
  // accept any string while keeping the well-known narrow set discoverable.
  look_type: "background" | "body_motion" | "tryon" | string;
  pose_angle?: string;
  product_id?: string;
  error_message: string | null;
  created_at: string;
}

// ── Voice Corpus ──

export interface VoiceCorpusEntry {
  id: string;
  avatar_id: string;
  source_type: "uploaded" | "tiktok_scrape";
  source_url?: string;
  audio_r2_key?: string;
  audio_url?: string;
  transcript?: string;
  duration_seconds?: number;
  status: "pending" | "processing" | "ready" | "failed";
  error_message?: string;
  created_at: string;
}

// ── Billing: subscriptions, PAYG credits, usage metering ──
// Mirrors services/billing_config.py + services/billing_service.py's
// get_billing_dashboard shape exactly — keep both sides in sync.

export type PlanId = "starter" | "pro" | "studio";
export type BillingIntervalId = "month" | "year";

export interface PlanConfig {
  name: string;
  monthly_price_cents: number;
  annual_price_cents: number;
  annual_monthly_equivalent_cents: number;
  avatar_slots: number;
  render_minutes_per_month: number;
  live_stream_hours_per_month: number;
  social_accounts_limit: number | null;
  production_level: "standard" | "premium";
  team_seats: number;
}

export interface CreditPack {
  amount_cents: number;
}

export interface PlansResponse {
  plans: Record<PlanId, PlanConfig>;
  free_tier: { name: string; avatar_slots: number; render_minutes: number; watermarked: boolean };
  credit_packs: Record<string, CreditPack>;
  overage_rates: {
    render_per_minute: { standard: number; premium: number };
    live_stream_per_hour: number;
    avatar_slot_per_month: number;
  };
  starter_fair_use_social_accounts: number;
}

export interface BillingDashboard {
  plan: PlanId | "free";
  interval: BillingIntervalId | null;
  status: "active" | "past_due" | "canceled" | "expired" | "free" | null;
  cancel_at_period_end: boolean;
  renewal_date: string | null;
  is_free_tier: boolean;
  render_minutes: { included: number; used: number; remaining: number };
  live_stream_hours: { included: number; used: number; remaining: number };
  avatar_slots: { included: number; purchased: number; total: number; used: number; remaining: number };
  credits: {
    balance_cents: number;
    auto_topup_enabled: boolean;
    auto_topup_threshold_cents: number | null;
    auto_topup_amount_cents: number | null;
  };
}

export interface CreditTransactionDto {
  id: string;
  type: "purchase" | "auto_topup" | "deduction" | "expiration" | "refund" | "adjustment";
  amount_cents: number;
  balance_after_cents: number;
  description: string | null;
  expires_at: string | null;
  created_at: string | null;
}
