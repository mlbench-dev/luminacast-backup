// ── Product Discovery ──

import type {
  CategoryTree,
  DiscoverResponse,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export const discoverApi = {
  trending: (params?: { section?: string; region?: string; page?: number; per_page?: number; sort_by?: string }) =>
    api.get<DiscoverResponse>("/discover/trending", { params }).then((r) => r.data),
  search: (params?: { q?: string; category?: string; min_price?: number; max_price?: number; min_revenue?: number; min_items_sold?: number; min_rating?: number; min_commission?: number; revenue_growth_min?: number; is_affiliate?: boolean; sort_by?: string; region?: string; page?: number; per_page?: number }) =>
    api.get<DiscoverResponse>("/discover/search", { params }).then((r) => r.data),
  categories: () =>
    api.get<{ categories: CategoryTree[] }>("/discover/categories").then((r) => r.data),
  importProduct: (tpId: string) =>
    api.post<{ product_id: string; message: string; already_imported: boolean }>(`/discover/import/${tpId}`).then((r) => r.data),
  refresh: (section?: string) =>
    api.post("/discover/refresh", null, { params: { section: section || "top_selling" } }).then((r) => r.data),
};
