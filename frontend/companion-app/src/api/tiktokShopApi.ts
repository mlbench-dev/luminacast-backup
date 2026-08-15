// ── TikTok Shop ──

import type {
  TikTokShopProduct,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const tiktokShopApi = {
  search: (query: string, maxProducts: number = 50) =>
    api.post<{ products: TikTokShopProduct[]; total: number }>("/products/search-tiktok-shop", { query, max_products: maxProducts }).then((r) => r.data),
  proxyImage: (imageUrl: string) =>
    api.post<{ image_url: string; r2_key: string }>("/products/proxy-product-image", { image_url: imageUrl }).then((r) => r.data),
};
