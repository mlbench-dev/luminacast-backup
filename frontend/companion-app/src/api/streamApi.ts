// ── Stream ──

import type {
  StreamStartRequest,
  StreamState,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const streamApi = {
  start: (data: StreamStartRequest) =>
    api.post<StreamState>("/stream/start", data).then((r) => r.data),
  stop: () =>
    api.post<StreamState>("/stream/stop").then((r) => r.data),
  skip: () =>
    api.post("/stream/skip").then((r) => r.data),
  pause: () =>
    api.post("/stream/pause").then((r) => r.data),
  pinConfirm: (productId: string) =>
    api.post("/stream/pin-confirm", { product_id: productId }).then((r) => r.data),
};
