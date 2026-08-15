// ── Channels ──

import type {
  Channel,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

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
