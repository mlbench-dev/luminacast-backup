import { create } from "zustand";
import type { StreamStatus, BlockType, LayoutMode } from "@/lib/types";

interface CurrentBlock {
  id: string;
  position: number;
  totalBlocks: number;
  type: BlockType;
  layoutMode: LayoutMode;
  productToPin: { product_id: string; name: string; price: number } | null;
  afkTimerSeconds: number | null;
  nextBlockPreview: { block_type: string; product_name: string | null } | null;
}

interface AfkState {
  secondsRemaining: number;
  productId: string;
  productName: string;
  triggered: boolean;
}

interface Operator {
  user_id: string;
  name: string;
  status: "online" | "idle";
}

interface StreamStoreState {
  sessionId: string | null;
  castId: string | null;
  status: StreamStatus | null;
  uptimeSeconds: number;
  viewers: number;
  totalPurchases: number;
  totalGmv: number;
  streamingCostCents: number;
  currentBlock: CurrentBlock | null;
  afk: AfkState | null;
  operators: Operator[];

  // Generation tracking
  generationProgress: number;
  generationStep: string | null;
  generationFailedCount: number;

  setSessionId: (id: string | null) => void;
  setCastId: (id: string | null) => void;
  updateStreamState: (data: { status: StreamStatus; uptime_seconds: number; viewers: number; total_purchases: number; total_gmv: number }) => void;
  updateBlock: (block: CurrentBlock) => void;
  updateAfk: (afk: AfkState | null) => void;
  updateOperators: (operators: Operator[]) => void;
  updateGeneration: (progress: number, step: string | null, failedCount: number) => void;
  reset: () => void;
}

const initialState = {
  sessionId: null,
  castId: null,
  status: null,
  uptimeSeconds: 0,
  viewers: 0,
  totalPurchases: 0,
  totalGmv: 0,
  streamingCostCents: 0,
  currentBlock: null,
  afk: null,
  operators: [],
  generationProgress: 0,
  generationStep: null,
  generationFailedCount: 0,
};

export const useStreamStore = create<StreamStoreState>((set) => ({
  ...initialState,

  setSessionId: (id) => set({ sessionId: id }),
  setCastId: (id) => set({ castId: id }),

  updateStreamState: (data) =>
    set({
      status: data.status,
      uptimeSeconds: data.uptime_seconds,
      viewers: data.viewers,
      totalPurchases: data.total_purchases,
      totalGmv: data.total_gmv,
    }),

  updateBlock: (block) => set({ currentBlock: block }),

  updateAfk: (afk) => set({ afk }),

  updateOperators: (operators) => set({ operators }),

  updateGeneration: (progress, step, failedCount) =>
    set({
      generationProgress: progress,
      generationStep: step,
      generationFailedCount: failedCount,
    }),

  reset: () => set(initialState),
}));
