// ── User profile (custom interests, etc) ──

import type {
  User,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const userApi = {
  getInterests: () =>
    api.get<{ custom_interests: string[] }>("/users/me/interests")
      .then((r) => r.data.custom_interests || []),
  setInterests: (interests: string[]) =>
    api.post<{ custom_interests: string[] }>("/users/me/interests", { interests })
      .then((r) => r.data.custom_interests || []),
  // Affiliate IDs (TikTok Shop, Amazon Associates). Empty / null `value`
  // clears the field — the backend treats whitespace-only strings the
  // same way to keep the UI free of trim() vs blanking edge cases.
  getAffiliates: () =>
    api.get<{ tiktok_affiliate_id: string | null; amazon_associate_tag: string | null }>(
      "/users/me/affiliates",
    ).then((r) => r.data),
  updateAffiliate: (platform: "tiktok" | "amazon", value: string | null) =>
    api.patch<{ tiktok_affiliate_id: string | null; amazon_associate_tag: string | null }>(
      "/users/me/affiliates",
      { platform, value },
    ).then((r) => r.data),
  updateProfile: (display_name: string | null) =>
    api.patch<User>("/users/me/profile", { display_name }).then((r) => r.data),
  uploadAvatar: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api
      .post<User>("/users/me/avatar", form, { headers: { "Content-Type": "multipart/form-data" } })
      .then((r) => r.data);
  },
  deleteAvatar: () => api.delete<User>("/users/me/avatar").then((r) => r.data),
};
