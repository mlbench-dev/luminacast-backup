// ── Analytics ──

import type {
  SessionSummary,
  VariantPerformance,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const analyticsApi = {
  sessions: () =>
    api.get<{ sessions: SessionSummary[]; total: number }>("/analytics/sessions").then((r) => r.data),
  session: (id: string) =>
    api.get<SessionSummary>(`/analytics/sessions/${id}`).then((r) => r.data),
  variants: () =>
    api.get<{ variants: VariantPerformance[]; total: number }>("/analytics/variants").then((r) => r.data),
};
