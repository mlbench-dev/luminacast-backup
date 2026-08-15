// ── userPhotosApi ──

import { api } from "@/lib/apiClient";

export const userPhotosApi = {
  list: () => api.get("/user-photos").then(r => r.data),
  upload: (file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return api.post("/user-photos/upload", form, { headers: { "Content-Type": "multipart/form-data" } }).then(r => r.data);
  },
  delete: (id: string) => api.delete("/user-photos/" + id).then(r => r.data),
};
