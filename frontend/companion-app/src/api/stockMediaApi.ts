// ── Stock Media (Pexels) ──

import { api } from "@/lib/apiClient";

export const stockMediaApi = {
  searchPhotos: (params: { q: string; orientation?: string; page?: number; per_page?: number }) =>
    api.get("/stock-media/photos", { params }).then((r) => r.data),
  searchVideos: (params: { q: string; orientation?: string; min_duration?: number; max_duration?: number; page?: number; per_page?: number }) =>
    api.get("/stock-media/videos", { params }).then((r) => r.data),
  importMedia: (data: { url: string; type: string; pexels_id: number; name?: string }) =>
    api.post("/stock-media/import", data).then((r) => r.data),
};
