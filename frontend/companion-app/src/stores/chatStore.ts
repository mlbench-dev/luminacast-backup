import { create } from "zustand";
import type { ChatMessage } from "@/lib/types";

interface ChatStoreState {
  messages: ChatMessage[];
  addMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  lockDraft: (messageId: string, lockedBy: string) => void;
  clearMessages: () => void;
}

export const useChatStore = create<ChatStoreState>((set) => ({
  messages: [],

  addMessage: (msg) =>
    set((state) => ({
      messages: [...state.messages, msg],
    })),

  updateMessage: (id, updates) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === id ? { ...m, ...updates } : m
      ),
    })),

  lockDraft: (messageId, lockedBy) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === messageId ? { ...m, locked_by: lockedBy } : m
      ),
    })),

  clearMessages: () => set({ messages: [] }),
}));
