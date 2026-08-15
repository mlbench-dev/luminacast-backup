// ── Chat ──

import type {
  ChatMessage,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const chatApi = {
  approve: (msgId: string, editedText?: string) =>
    api.post<ChatMessage>(`/chat/approve/${msgId}`, { edited_text: editedText ?? null }).then((r) => r.data),
  reject: (msgId: string) =>
    api.post<ChatMessage>(`/chat/reject/${msgId}`).then((r) => r.data),
  send: (sessionId: string, text: string, mode: "chat_only" | "voice_and_chat" = "chat_only") =>
    api.post("/chat/send", { session_id: sessionId, text, mode }).then((r) => r.data),
  lock: (msgId: string) =>
    api.post(`/chat/lock/${msgId}`).then((r) => r.data),
};
