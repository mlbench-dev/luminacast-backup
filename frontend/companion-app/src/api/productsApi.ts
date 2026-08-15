// ── Products ──

import type {
  ProductAsset,
  ProductCreate,
  ProductDetailResponse,
  ProductWithAssets,
  TikTokShopProduct,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export interface NeedsManualEntry {
  status: "needs_manual_entry";
  source: string;
  source_product_id: string | null;
  source_url: string;
  message: string;
}

export type FromUrlResult =
  | (ProductWithAssets & { already_existed?: boolean })
  | NeedsManualEntry;

export function isNeedsManualEntry(r: FromUrlResult): r is NeedsManualEntry {
  return (r as NeedsManualEntry)?.status === "needs_manual_entry";
}

export const productsApi = {
  list: (params?: { page?: number; per_page?: number; search?: string; sort?: string; filter?: string }) =>
    api.get<{ products: ProductWithAssets[]; total: number; page: number; per_page: number }>("/products", { params }).then((r) => r.data),
  get: (id: string) =>
    api.get<ProductDetailResponse>(`/products/${id}`).then((r) => r.data),
  create: (data: ProductCreate & Record<string, unknown>) =>
    api.post<ProductWithAssets>("/products", data).then((r) => r.data),
  // Returns the imported product on 201, or a needs_manual_entry payload on
  // 202 when the source (e.g. TikTok Shop) blocked the automated lookup.
  fromUrl: (url: string): Promise<FromUrlResult> =>
    api
      .post<ProductWithAssets & { already_existed?: boolean } | NeedsManualEntry>(
        "/products/from-url",
        { url },
      )
      .then((r) => {
        if (r.status === 202 && (r.data as NeedsManualEntry)?.status === "needs_manual_entry") {
          return r.data as NeedsManualEntry;
        }
        return r.data as ProductWithAssets & { already_existed?: boolean };
      }),
  refresh: (id: string) =>
    api.post<ProductWithAssets>(`/products/${id}/refresh`).then((r) => r.data),
  update: (id: string, data: Partial<ProductCreate> & Record<string, unknown>) =>
    api.put<ProductWithAssets>(`/products/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api.delete(`/products/${id}`),
  getAssets: (productId: string) =>
    api.get<ProductAsset[]>(`/products/${productId}/assets`).then((r) => r.data),
  uploadAsset: (productId: string, file: File, assetType: string = "product_shot") => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post<ProductAsset>(`/products/${productId}/assets/upload?asset_type=${assetType}`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },
  deleteAsset: (productId: string, assetId: string) =>
    api.delete(`/products/${productId}/assets/${assetId}`),
  generateOverlay: (productId: string) =>
    api.post(`/products/${productId}/generate-overlay`).then((r) => r.data),
  generateAiImages: (productId: string, style?: string, customPrompt?: string) =>
    api.post(`/products/${productId}/generate-ai-images`, { style: style || "product_shot", custom_prompt: customPrompt || "" }).then((r) => r.data),
  generateAiVideo: (productId: string, style?: string, duration?: number, customPrompt?: string, quality?: string) =>
    api.post(`/products/${productId}/generate-ai-video`, { style: style || "product_showcase", duration_seconds: duration || 5, custom_prompt: customPrompt || "", quality: quality || "pro" }).then((r) => r.data),
  removeBackground: (productId: string) =>
    api.post(`/products/${productId}/remove-background`).then((r) => r.data),
  importTiktok: (query: string, maxProducts?: number) =>
    api.post<{ products: TikTokShopProduct[]; total: number }>("/products/import-tiktok", { query, max_products: maxProducts || 50 }).then((r) => r.data),
  generateReviews: (productId: string) =>
    api.post<{
      reviews: Array<{ id: string; stars: number; author: string; text: string; verified: boolean; date: string }>;
      generated_at: string;
      model_used: string;
    }>(`/products/${productId}/generate-reviews`).then((r) => r.data),
};
