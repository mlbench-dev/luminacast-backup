// ── User action history (audit log) ──

import { api } from "@/lib/apiClient";

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
