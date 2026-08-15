// ── generatedVideosApi ──

import { api } from "@/lib/apiClient";

export const generatedVideosApi = {
  delete: (id: string) => api.delete("/videos/generated/" + id).then(r => r.data),
};
