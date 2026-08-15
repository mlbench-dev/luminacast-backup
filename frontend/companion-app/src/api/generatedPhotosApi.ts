// ── generatedPhotosApi ──

import { api } from "@/lib/apiClient";

export const generatedPhotosApi = {
  delete: (id: string) => api.delete("/photos/generated/" + id).then(r => r.data),
};
