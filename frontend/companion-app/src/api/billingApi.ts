import { api } from "@/lib/apiClient";
import type {
  PlansResponse,
  BillingDashboard,
  CreditTransactionDto,
  PlanId,
  BillingIntervalId,
} from "@/lib/types";

// ── Billing: subscriptions, PAYG credits, usage metering ──

export const billingApi = {
  getPlans: () => api.get<PlansResponse>("/billing/plans").then((r) => r.data),
  getDashboard: () => api.get<BillingDashboard>("/billing/dashboard").then((r) => r.data),
  checkoutSubscription: (plan: PlanId, interval: BillingIntervalId) =>
    api
      .post<
        | { url: string; id: string }
        | { status: "updated"; plan: PlanId; interval: BillingIntervalId; renewal_date: string }
      >("/billing/checkout/subscription", { plan, interval })
      .then((r) => r.data),
  checkoutCredits: (packId: string) =>
    api.post<{ url: string; id: string }>("/billing/checkout/credits", { pack_id: packId }).then((r) => r.data),
  openPortal: (returnUrl?: string) =>
    api.post<{ url: string }>("/billing/portal", { return_url: returnUrl }).then((r) => r.data),
  cancelSubscription: () =>
    api.post<{ status: string; cancel_at_period_end: boolean; renewal_date: string | null }>(
      "/billing/cancel",
      {},
    ).then((r) => r.data),
  purchaseAvatarSlot: () =>
    api.post<{ avatar_slot_purchase_id: string; rate_cents: number }>(
      "/billing/avatar-slots/purchase",
      {},
    ).then((r) => r.data),
  listCreditTransactions: (limit = 50) =>
    api
      .get<{ transactions: CreditTransactionDto[] }>("/billing/credits/transactions", { params: { limit } })
      .then((r) => r.data.transactions),
  setAutoTopup: (data: { enabled: boolean; threshold_cents?: number; amount_cents?: number }) =>
    api.post<{ auto_topup_enabled: boolean; auto_topup_threshold_cents: number | null; auto_topup_amount_cents: number | null }>(
      "/billing/credits/auto-topup",
      data,
    ).then((r) => r.data),
};
