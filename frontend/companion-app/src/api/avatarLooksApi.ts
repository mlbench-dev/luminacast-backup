// ── avatarLooksApi ──

import { api } from "@/lib/apiClient";

export const avatarLooksApi = {
  list: (avatarId: string) =>
    api.get(`/avatar/${avatarId}/looks`).then((r) => r.data),
  create: (avatarId: string, payload: { name: string; background_prompt?: string; look_type?: string; pose_angle?: string; product_id?: string; environment?: string; mic_visible?: boolean }) =>
    api.post(`/avatar/${avatarId}/looks`, payload).then((r) => r.data),
  delete: (avatarId: string, lookId: string) =>
    api.delete(`/avatar/${avatarId}/looks/${lookId}`).then((r) => r.data),
  /** Flip a scene's own environment/mic-visible in place — decided when the
   * scene is created or any time after, without regenerating the image. */
  update: (avatarId: string, lookId: string, payload: { environment?: string; mic_visible?: boolean }) =>
    api.patch(`/avatar/${avatarId}/looks/${lookId}`, payload).then((r) => r.data),
  setDefault: (avatarId: string, lookId: string) =>
    api.post(`/avatar/${avatarId}/looks/${lookId}/set-default`).then((r) => r.data),
  generateAllBodyMotion: (avatarId: string) =>
    api.post(`/avatar/${avatarId}/generate-all-body-motion`).then((r) => r.data),
  /** Regenerate ONE Body-Motion pose. Routes through the avatar's Body Shot
   * pipeline (full-body canonical reference) and re-syncs the look, so the
   * "Shots" step and the "Body Motion" tab stay identical. Falls back to the
   * standalone look pipeline for avatars with no Body Shot set. */
  regenerateBodyMotionPose: (avatarId: string, pose: string) =>
    api.post(`/avatar/ai/regenerate-body-motion-pose`, { avatar_id: avatarId, pose }).then((r) => r.data),
};
