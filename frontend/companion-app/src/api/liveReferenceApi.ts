import type { AxiosProgressEvent } from "axios";
import { api } from "@/lib/api";

export type LiveReferenceStatus =
  | "uploaded"
  | "transcribing"
  | "transcribed"
  | "assessed"
  | "failed";

export interface LiveReference {
  id: string;
  avatar_id: string | null;
  cast_id: string | null;
  user_id: string;
  source_r2_key: string | null;
  source_url: string | null;
  media_kind: "audio" | "video";
  duration_seconds: number | null;
  transcript_text: string | null;
  transcript_segments: Array<{ text: string; start: number; end: number }> | null;
  assessment: Record<string, unknown> | null;
  status: LiveReferenceStatus;
  error_message: string | null;
  is_active: boolean;
  created_at: string | null;
}

interface UploadArgs {
  file: File;
  avatarId?: string;
  castId?: string;
  onProgress?: (pct: number) => void;
}

export const liveReferenceApi = {
  uploadLiveReference: ({ file, avatarId, castId, onProgress }: UploadArgs) => {
    const form = new FormData();
    form.append("file", file);
    if (avatarId) form.append("avatar_id", avatarId);
    if (castId) form.append("cast_id", castId);
    return api
      .post<{ live_reference_id: string; status: LiveReferenceStatus }>(
        "/live-references",
        form,
        {
          headers: { "Content-Type": "multipart/form-data" },
          timeout: 600000,
          onUploadProgress: (e: AxiosProgressEvent) => {
            if (onProgress && e.total) {
              onProgress(Math.round((e.loaded / e.total) * 100));
            }
          },
        },
      )
      .then((r) => r.data);
  },

  getLiveReference: (id: string) =>
    api.get<LiveReference>(`/live-references/${id}`).then((r) => r.data),

  listLiveReferences: ({ avatarId, castId }: { avatarId?: string; castId?: string }) => {
    const params: Record<string, string> = {};
    if (avatarId) params.avatar_id = avatarId;
    if (castId) params.cast_id = castId;
    return api
      .get<{ live_references: LiveReference[]; total: number }>("/live-references", { params })
      .then((r) => r.data);
  },

  deleteLiveReference: (id: string) =>
    api.delete<{ ok: boolean }>(`/live-references/${id}`).then((r) => r.data),

  activateLiveReference: (id: string) =>
    api.post<{ ok: boolean }>(`/live-references/${id}/activate`).then((r) => r.data),
};
