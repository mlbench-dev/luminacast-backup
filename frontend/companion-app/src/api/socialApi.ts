// ── Social media (Zernio) ──

import { api } from "@/lib/apiClient";

export interface SocialChannel {
  id: string;
  user_id: string;
  platform: string;
  platform_account_id?: string | null;
  handle?: string | null;
  display_name?: string | null;
  follower_count: number;
  profile_image_url?: string | null;
  zernio_account_id?: string | null;
  primary_avatar_id?: string | null;
  primary_avatar?: {
    id: string;
    name?: string;
    face_image_url?: string | null;
    face_ref_key?: string | null;
  } | null;
  avatar_history: Array<{
    avatar_id: string;
    avatar_name?: string;
    post_count: number;
    first_used?: string;
    last_used?: string;
  }>;
  total_posts: number;
  total_scheduled: number;
  status: "active" | "disconnected" | "token_expired";
  connected_at?: string | null;
  last_seen_at?: string | null;
}

export interface AvatarConsistencyResponse {
  status: "match" | "new_channel" | "mismatch";
  primary_avatar_id?: string | null;
  primary_avatar?: { id: string; name?: string } | null;
  channel_total_posts?: number;
}

export const socialApi = {
  listProfiles: () =>
    // Zernio's GET /accounts shape (see docs.zernio.com/api/openapi):
    // {_id, platform, username, displayName, profileUrl, isActive, ...} —
    // NOT {id, label, handle}, which never matched anything real and made
    // every connected account look unconnected on the frontend.
    api.get<Array<{ _id: string; platform: string; username?: string; displayName?: string; isActive?: boolean }>>(
      "/social/profiles"
    ).then((r) => r.data),
  connectPlatform: (platform: string, redirectUri?: string) =>
    api.post<{ auth_url: string; platform: string }>(
      "/social/connect", { platform, redirect_uri: redirectUri },
    ).then((r) => r.data),
  // Call right after the OAuth popup reports success — this is what
  // actually attributes the newly-connected account to the current user.
  // accountId comes straight from Zernio's own callback redirect (it tells
  // us exactly which account was just connected), so pass it through
  // whenever we have it — the backend claims that exact account directly
  // instead of falling back to diffing account lists against a snapshot.
  confirmConnect: (platform: string, accountId?: string | null) =>
    api.post<{ claimed: boolean; zernio_account_ids?: string[]; reason?: string }>(
      "/social/connect/confirm", { platform, account_id: accountId || undefined },
    ).then((r) => r.data),
  // The OAuth redirect only ever carries a generic error code
  // ("connection_failed") — this looks up Zernio's activity log for the
  // real reason (e.g. "no YouTube channel on this Google account").
  // Best-effort: detail is null when nothing recent is found.
  getConnectError: (platform: string) =>
    api.get<{ detail: string | null }>(
      "/social/connect-error", { params: { platform } },
    ).then((r) => r.data),
  generateCaption: (data: { cast_id: string; platform: string }) =>
    api.post<{ caption: string; hashtags: string[]; first_comment: string | null }>(
      "/social/generate-caption", data
    ).then((r) => r.data),
  createPost: (data: {
    cast_id: string;
    render_id?: string;
    caption: string;
    hashtags: string[];
    first_comment?: string | null;
    platforms: Array<{ platform: string; accountId?: string; scheduled_for?: string }>;
    scheduled_for?: string | null;
    publish_now?: boolean;
  }) => api.post<Record<string, any>>("/social/posts", data).then((r) => r.data),
  listPosts: (opts?: { castId?: string; status?: string }) =>
    api.get<Array<Record<string, any>>>("/social/posts", {
      params: { cast_id: opts?.castId, status: opts?.status },
    }).then((r) => r.data),
  getPost: (postId: string) =>
    api.get<Record<string, any>>(`/social/posts/${postId}`).then((r) => r.data),
  getPendingCommentCount: () =>
    api.get<{ count: number }>("/social/comments/pending-count").then((r) => r.data),
  deletePost: (postId: string) =>
    api.delete(`/social/posts/${postId}`).then((r) => r.data),
  getComments: (postId: string) =>
    api.get<Array<Record<string, any>>>(`/social/posts/${postId}/comments`).then((r) => r.data),
  reply: (postId: string, commentId: string, text?: string) =>
    api.post(`/social/posts/${postId}/comments/${commentId}/reply`, { text }).then((r) => r.data),
  skip: (postId: string, commentId: string) =>
    api.post(`/social/posts/${postId}/comments/${commentId}/skip`).then((r) => r.data),

  // ── Channels (Publish hub) ───────────────────────────────────────────
  listChannels: () =>
    api.get<{ channels: SocialChannel[] }>("/social/channels").then((r) => r.data.channels),
  disconnectChannel: (channelId: string) =>
    api.delete<{ ok: boolean }>(`/social/channels/${channelId}`).then((r) => r.data),
  checkAvatarConsistency: (channelId: string, avatarId: string) =>
    api.get<AvatarConsistencyResponse>(
      `/social/channels/${channelId}/avatar-check`, { params: { avatar_id: avatarId } },
    ).then((r) => r.data),
};

// confirmConnect depends on a live call to Zernio's own accounts API, which
// has been observed to be slow enough that a request can outlast whatever
// proxy/gateway sits in front of the backend — the browser sees an error (or
// a clean claimed:false) while the backend keeps running and commits the new
// channel row moments later. A single attempt can't tell "genuinely
// unclaimable" apart from "not there yet", so retry a few times with a short
// delay before giving up; each retry re-runs the same idempotent diff, so
// re-checking is safe even if the first attempt actually did land.
export async function confirmConnectWithRetry(
  platform: string,
  accountId?: string | null,
  attempts = 3,
  delayMs = 2500,
): Promise<{ claimed: boolean; zernio_account_ids?: string[]; reason?: string }> {
  let last: { claimed: boolean; zernio_account_ids?: string[]; reason?: string } = { claimed: false };
  for (let i = 0; i < attempts; i++) {
    try {
      last = await socialApi.confirmConnect(platform, accountId);
    } catch (err) {
      console.error("confirmConnect failed:", err);
      last = { claimed: false };
    }
    if (last.claimed) return last;
    // "owned_by_other_user" is a definitive answer, not a propagation-delay
    // race — the account belongs to a different Luminacast login and no
    // amount of retrying against Zernio's API changes that. Retrying here
    // just makes the user wait ~7.5s for a toast that was already decided.
    if (last.reason === "owned_by_other_user") return last;
    if (i < attempts - 1) await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return last;
}
