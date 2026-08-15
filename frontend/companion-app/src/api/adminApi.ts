// ── Admin ──

import type {
  AdminCreator,
  AdminDashboard,
  AdminPrompt,
  AdminPromptDetail,
  AdminStream,
  AdminSystemStatus,
  AdminUsageByService,
  AdminUsageByUser,
  AdminUsageDaily,
  AdminUsageRecent,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

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
