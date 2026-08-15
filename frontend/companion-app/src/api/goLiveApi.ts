// ── Go Live (multi-cast / multi-platform / monitor) ──

import { api } from "@/lib/apiClient";

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
