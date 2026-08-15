// ── User Videos ──

import { api } from "@/lib/apiClient";

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
