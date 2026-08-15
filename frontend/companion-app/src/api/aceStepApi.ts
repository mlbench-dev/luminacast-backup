// ── Music ──

import { api } from "@/lib/apiClient";

// Used by castsApi (not aceStepApi's own methods) — imported from there.
export interface MusicLibraryTrack {
  id: string;
  name: string;
  mood: string;
  url: string;
}

export interface MusicSoundCast {
  id: string;
  name: string;
  description: string;
  status: string;
  training_audio_count: number;
  training_audio_keys: string[];
  training_prompts: string[];
  training_steps: number;
  training_loss: number;
  training_error: string;
  lora_r2_key: string;
  created_at: string | null;
}

export interface MusicTrackItem {
  id: string;
  sound_cast_id: string;
  name: string;
  prompt: string;
  lyrics: string;
  status: string;
  audio_url: string;
  duration_seconds: number;
  generation_time_seconds: number;
  generation_error: string;
  seed: number;
  guidance_scale: number;
  inference_steps: number;
  scheduler_type: string;
  created_at: string | null;
}

export const aceStepApi = {
  listSoundCasts: () =>
    api.get<{ sound_casts: MusicSoundCast[] }>("/music/sound-casts").then((r) => r.data.sound_casts),
  getSoundCast: (id: string) =>
    api.get<MusicSoundCast>("/music/sound-casts/" + id).then((r) => r.data),
  createSoundCast: (data: { name: string; description?: string }) =>
    api.post<MusicSoundCast>("/music/sound-casts", data).then((r) => r.data),
  deleteSoundCast: (id: string) =>
    api.delete("/music/sound-casts/" + id),
  uploadTrainingAudio: (scId: string, file: File, prompt: string) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("prompt", prompt || "");
    return api.post<MusicSoundCast>("/music/sound-casts/" + scId + "/training-audio", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  deleteTrainingAudio: (scId: string, audioIdx: number) =>
    api.delete<MusicSoundCast>("/music/sound-casts/" + scId + "/training-audio/" + audioIdx).then((r) => r.data),
  startTraining: (scId: string, data: { steps?: number }) =>
    api.post<MusicSoundCast>("/music/sound-casts/" + scId + "/train", { training_prompts: [], steps: data.steps || 2400 }).then((r) => r.data),
  cancelTraining: (scId: string) =>
    api.post<MusicSoundCast>("/music/sound-casts/" + scId + "/cancel-training").then((r) => r.data),
  listTracks: (scId: string) =>
    api.get<{ tracks: MusicTrackItem[] }>("/music/sound-casts/" + scId + "/tracks").then((r) => r.data.tracks),
  generateTrack: (scId: string, data: {
    name: string; prompt: string; lyrics?: string; duration_seconds?: number;
    seed?: number; guidance_scale?: number; inference_steps?: number; scheduler_type?: string;
  }) =>
    api.post<MusicTrackItem>("/music/sound-casts/" + scId + "/tracks", data).then((r) => r.data),
  deleteTrack: (trackId: string) =>
    api.delete("/music/tracks/" + trackId),
};
