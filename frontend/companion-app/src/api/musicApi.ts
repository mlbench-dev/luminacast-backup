// ── musicApi ──

import { api } from "@/lib/apiClient";

export interface CatalogTrack {
  id: string;
  name: string;
  mood: string;
  intensity: string;
  tempo: string;
  duration: number;
  prompt: string;
  url: string;
  created_at: string | null;
}

export interface UploadedTrack {
  id: string;
  name: string;
  url: string;
  duration: number | null;
  file_size_bytes: number;
  created_at: string | null;
}

export interface SfxItem {
  key: string;
  icon: string;
  label: string;
  duration: number;
  description: string;
  url: string;
}

export interface AIGeneratedTrack {
  id: string;
  url: string;
  duration: number;
  prompt: string;
  name?: string;
  mood?: string | null;
  intensity?: string | null;
  bpm?: number | null;
  key?: string | null;
  created_at?: string | null;
}

export interface LibraryParamOption {
  label: string;
  value: string | number;
}

export interface LibraryParams {
  genres: LibraryParamOption[];
  moods: LibraryParamOption[];
  activities: LibraryParamOption[];
  bpms: LibraryParamOption[];
  durations: LibraryParamOption[];
}

export interface LibraryTrack {
  id: string;
  name: string;
  description: string;   // pre-formatted "120 BPM · C#m · high" line from backend
  mood: string;
  genre: string;
  moods: string[];
  genres: string[];
  intensity: string;
  mode: string;          // jingle | mix | track
  tempo: string;
  bpm: number | null;
  key: string;
  duration: number;
  prompt: string;
  url: string;
  created_at: string | null;
}

export interface LibraryTracksResponse {
  tracks: LibraryTrack[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export const musicApi = {
  catalog: (params?: { mood?: string; duration?: number; limit?: number }) =>
    api.get<{ tracks: CatalogTrack[]; total: number }>("/music/catalog", { params }).then((r) => r.data),
  libraryParams: () =>
    api.get<LibraryParams>("/music/library/params").then((r) => r.data),
  libraryTracks: (params: {
    genre?: string;
    mood?: string;
    activity?: string;
    bpm?: string;
    duration?: number;
    offset?: number;
    limit?: number;
  }) =>
    api
      .get<LibraryTracksResponse>("/music/library/tracks", { params })
      .then((r) => r.data),
  generate: (req: {
    prompt?: string;
    mood?: string;
    duration_seconds: number;
    intensity: string;
    cast_id?: string;
  }) => api.post<AIGeneratedTrack>("/music/ai/generate", req).then((r) => r.data),
  generatedList: () =>
    api.get<{ tracks: AIGeneratedTrack[]; total: number }>("/music/ai/generated").then((r) => r.data),
  generatedSave: (track: AIGeneratedTrack) =>
    api.post<{ success: boolean }>("/music/ai/generated", track).then((r) => r.data),
  generatedDelete: (id: string) =>
    api.delete<{ success: boolean }>("/music/ai/generated/" + id).then((r) => r.data),
  uploadedList: () =>
    api.get<{ tracks: UploadedTrack[]; total: number }>("/music/uploaded").then((r) => r.data),
  uploadedUpload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api
      .post<{ id: string; name: string; url: string; file_size_bytes: number }>(
        "/music/uploaded/upload",
        fd,
        { headers: { "Content-Type": "multipart/form-data" } },
      )
      .then((r) => r.data);
  },
  uploadedDelete: (id: string) =>
    api.delete<{ deleted: boolean }>("/music/uploaded/" + id).then((r) => r.data),
  sfxLibrary: () =>
    api.get<{ items: SfxItem[] }>("/music/sfx/library").then((r) => r.data),
};
