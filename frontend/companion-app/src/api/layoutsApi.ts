// ── Layout Templates ──

import type {
  LayoutTemplate,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

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
