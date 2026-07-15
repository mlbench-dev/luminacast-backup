import { useEffect, useRef, useCallback } from "react";
import { useAuthStore } from "@/stores/authStore";
import { useStreamStore } from "@/stores/streamStore";
import { useChatStore } from "@/stores/chatStore";
import type { WSServerEvent, WSClientEvent, StreamStatus, BlockType, LayoutMode } from "@/lib/types";

const WS_BASE = import.meta.env.VITE_WS_URL || `ws://${window.location.host}/ws`;

interface UseWebSocketOptions {
  sessionId: string | null;
  enabled?: boolean;
}

export function useWebSocket({ sessionId, enabled = true }: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | undefined>(undefined);
  const token = useAuthStore((s) => s.token);
  const updateStreamState = useStreamStore((s) => s.updateStreamState);
  const updateBlock = useStreamStore((s) => s.updateBlock);
  const updateAfk = useStreamStore((s) => s.updateAfk);
  const updateOperators = useStreamStore((s) => s.updateOperators);
  const updateGeneration = useStreamStore((s) => s.updateGeneration);
  const addMessage = useChatStore((s) => s.addMessage);
  const lockDraft = useChatStore((s) => s.lockDraft);

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as WSServerEvent;

        switch (data.type) {
          case "STREAM_STATE_UPDATE":
            updateStreamState(data.payload);
            break;

          case "BLOCK_TRANSITION":
            updateBlock({
              id: data.payload.current_block_id,
              position: data.payload.current_block_position,
              totalBlocks: data.payload.total_blocks,
              type: data.payload.block_type as BlockType,
              layoutMode: data.payload.layout_mode as LayoutMode,
              productToPin: data.payload.product_to_pin,
              afkTimerSeconds: data.payload.afk_timer_seconds,
              nextBlockPreview: data.payload.next_block_preview,
            });
            break;

          case "NEW_CHAT_MESSAGE":
            addMessage({
              id: data.payload.message_id,
              session_id: sessionId || "",
              viewer_username: data.payload.viewer_username,
              message_text: data.payload.message_text,
              is_purchase: data.payload.is_purchase,
              ai_draft: data.payload.ai_draft ?? undefined,
              ai_draft_status: data.payload.ai_draft_status ?? "pending",
              timestamp: new Date().toISOString(),
            });
            break;

          case "CHAT_DRAFT_LOCKED":
            lockDraft(data.payload.message_id, data.payload.locked_by);
            break;

          case "AFK_TIMER_TICK":
            updateAfk({
              secondsRemaining: data.payload.seconds_remaining,
              productId: data.payload.product_id,
              productName: data.payload.product_name,
              triggered: false,
            });
            break;

          case "AFK_TRIGGERED":
            updateAfk({
              secondsRemaining: 0,
              productId: data.payload.product_id,
              productName: "",
              triggered: true,
            });
            break;

          case "OPERATOR_PRESENCE":
            updateOperators(data.payload.operators);
            break;

          case "GENERATION_PROGRESS":
            updateGeneration(
              data.payload.progress,
              data.payload.current_step,
              data.payload.failed_count
            );
            break;
        }
      } catch {
        // Ignore malformed messages
      }
    },
    [sessionId, updateStreamState, updateBlock, addMessage, lockDraft, updateAfk, updateOperators, updateGeneration]
  );

  useEffect(() => {
    if (!enabled || !sessionId || !token) return;

    const connect = () => {
      const ws = new WebSocket(`${WS_BASE}/stream/${sessionId}?token=${token}`);
      wsRef.current = ws;

      ws.onmessage = handleMessage;
      ws.onclose = () => {
        reconnectTimeoutRef.current = window.setTimeout(connect, 3000);
      };
      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimeoutRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [sessionId, token, enabled, handleMessage]);

  const send = useCallback((event: WSClientEvent) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(event));
    }
  }, []);

  const isConnected = wsRef.current?.readyState === WebSocket.OPEN;

  return { send, isConnected };
}
