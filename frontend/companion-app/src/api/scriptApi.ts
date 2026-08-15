// ── Script Rewrite ──

import { api } from "@/lib/apiClient";

export const scriptApi = {
  rewrite: (castId: string, blockId: string, variantId: string, prompt: string) =>
    api.post<{ script_text: string }>(`/casts/${castId}/blocks/${blockId}/variants/${variantId}/rewrite`, { prompt }).then((r) => r.data),
  rewriteInVoice: (castId: string, blockId: string) =>
    api.post<{ original: string; rewritten: string; corpus_entries_used: number }>(`/casts/${castId}/blocks/${blockId}/rewrite-in-voice`).then((r) => r.data),
  refineAllBlocks: (castId: string, instruction: string) =>
    api.post<{ updated: { block_id: string; text: string }[] }>(`/casts/${castId}/refine-all-blocks`, { instruction }).then((r) => r.data),
};
