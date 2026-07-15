import { describe, it, expect, beforeEach } from "vitest";
import { useStreamStore } from "@/stores/streamStore";
import { StreamStatus, BlockType, LayoutMode } from "@/lib/types";

describe("streamStore", () => {
  beforeEach(() => {
    useStreamStore.getState().reset();
  });

  it("should initialize with null session", () => {
    const state = useStreamStore.getState();
    expect(state.sessionId).toBeNull();
    expect(state.status).toBeNull();
    expect(state.viewers).toBe(0);
  });

  it("should update stream state", () => {
    useStreamStore.getState().updateStreamState({
      status: StreamStatus.LIVE,
      uptime_seconds: 120,
      viewers: 45,
      total_purchases: 3,
      total_gmv: 89.97,
    });

    const state = useStreamStore.getState();
    expect(state.status).toBe(StreamStatus.LIVE);
    expect(state.uptimeSeconds).toBe(120);
    expect(state.viewers).toBe(45);
    expect(state.totalPurchases).toBe(3);
    expect(state.totalGmv).toBe(89.97);
  });

  it("should update current block", () => {
    useStreamStore.getState().updateBlock({
      id: "blk_001",
      position: 2,
      totalBlocks: 8,
      type: BlockType.PRODUCT,
      layoutMode: LayoutMode.AVATAR_PRODUCT,
      productToPin: { product_id: "prod_1", name: "Serum", price: 29.99 },
      afkTimerSeconds: 60,
      nextBlockPreview: { block_type: "flash_sale", product_name: null },
    });

    const block = useStreamStore.getState().currentBlock;
    expect(block?.id).toBe("blk_001");
    expect(block?.position).toBe(2);
    expect(block?.productToPin?.name).toBe("Serum");
  });

  it("should update AFK state", () => {
    useStreamStore.getState().updateAfk({
      secondsRemaining: 30,
      productId: "prod_1",
      productName: "Glow Serum",
      triggered: false,
    });

    const afk = useStreamStore.getState().afk;
    expect(afk?.secondsRemaining).toBe(30);
    expect(afk?.triggered).toBe(false);
  });

  it("should update operators", () => {
    useStreamStore.getState().updateOperators([
      { user_id: "usr_1", name: "Alice", status: "online" },
      { user_id: "usr_2", name: "Bob", status: "idle" },
    ]);

    expect(useStreamStore.getState().operators).toHaveLength(2);
  });

  it("should update generation progress", () => {
    useStreamStore.getState().updateGeneration(0.75, "Generating audio 18/24", 2);

    const state = useStreamStore.getState();
    expect(state.generationProgress).toBe(0.75);
    expect(state.generationStep).toBe("Generating audio 18/24");
    expect(state.generationFailedCount).toBe(2);
  });

  it("should reset to initial state", () => {
    useStreamStore.getState().setSessionId("ses_123");
    useStreamStore.getState().updateStreamState({
      status: StreamStatus.LIVE,
      uptime_seconds: 500,
      viewers: 100,
      total_purchases: 10,
      total_gmv: 299.90,
    });
    useStreamStore.getState().reset();

    const state = useStreamStore.getState();
    expect(state.sessionId).toBeNull();
    expect(state.status).toBeNull();
    expect(state.viewers).toBe(0);
  });
});
