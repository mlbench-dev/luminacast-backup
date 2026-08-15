// ── voiceCorpusApi ──

import { api } from "@/lib/apiClient";

export const voiceCorpusApi = {
  list: (avatarId: string) =>
    api.get(`/avatar/${avatarId}/voice-corpus`).then((r) => r.data),
  upload: (avatarId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post(`/avatar/${avatarId}/voice-corpus/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  uploadUrl: (avatarId: string, sourceUrl: string) => {
    const form = new FormData();
    form.append("source_url", sourceUrl);
    return api.post(`/avatar/${avatarId}/voice-corpus/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  delete: (avatarId: string, entryId: string) =>
    api.delete(`/avatar/${avatarId}/voice-corpus/${entryId}`).then((r) => r.data),
  trainFromCorpus: (avatarId: string, corpusEntryIds: string[]) =>
    api.post<{ voice_id: string; voice_name: string }>(
      `/avatar/ai/${avatarId}/clone-voice-from-corpus`,
      { corpus_entry_ids: corpusEntryIds },
    ).then((r) => r.data),
};
