// ── liveSessionApi ──

import { api } from "@/lib/apiClient";

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
