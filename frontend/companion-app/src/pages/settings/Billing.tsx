import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CreditCard, Sparkles, Users2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { billingApi, api } from "@/lib/api";
import { useToast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";
import type { BillingDashboard } from "@/lib/types";

type UsageSummary = {
  period_days: number;
  total_price: number;
  render_count: number;
  render_price: number;
  music_price: number;
  body_shot_price: number;
  publish_price: number;
  by_type: Record<string, { price: number; count: number }>;
};

function MetricCard({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-xs text-text-muted uppercase tracking-wider">{label}</div>
        <div className="mt-1 text-xl font-semibold text-text">{value}</div>
      </CardContent>
    </Card>
  );
}

function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "$0.00";
  return `$${n.toFixed(2)}`;
}

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function BillingRow({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="flex justify-between py-2 text-sm border-b border-border last:border-0">
      <span className="text-text-dim">{label}</span>
      <span className="text-text">{formatUsd(value)}</span>
    </div>
  );
}

function UsageMeter({
  label,
  used,
  included,
  unit,
}: {
  label: string;
  used: number;
  included: number;
  unit: string;
}) {
  const pct = included > 0 ? Math.min(100, (used / included) * 100) : 0;
  const remaining = Math.max(included - used, 0);
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-sm">
        <span className="text-text-dim">{label}</span>
        <span className="text-text">
          {used.toFixed(1)} / {included.toFixed(1)} {unit}
        </span>
      </div>
      <Progress value={pct} />
      <div className="text-xs text-text-muted">
        {remaining.toFixed(1)} {unit} remaining
      </div>
    </div>
  );
}

const PLAN_LABEL: Record<string, string> = {
  free: "Free",
  starter: "Starter",
  pro: "Pro",
  studio: "Studio",
};

const STATUS_BADGE: Record<string, "success" | "warning" | "danger" | "secondary"> = {
  active: "success",
  past_due: "warning",
  canceled: "danger",
  expired: "danger",
  free: "secondary",
};

export function BillingPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data: usage, isLoading: usageLoading } = useQuery<UsageSummary>({
    queryKey: ["my-usage", 30],
    queryFn: () => api.get<UsageSummary>("/usage/summary?days=30").then((r) => r.data),
  });

  const { data: dashboard, isLoading: dashboardLoading, error: dashboardError } = useQuery<BillingDashboard>({
    queryKey: ["billing-dashboard"],
    queryFn: () => billingApi.getDashboard(),
    // Billing is owner-only (routers/billing.py: require_owner) — a team
    // member viewing this while switched into someone else's workspace
    // always gets 403, which will never resolve on retry. Without this,
    // the default retry (3x) delays the error, and since the loading
    // check below was `dashboardLoading || !dashboard` — true forever on
    // a permanent error — the page showed an endless loading skeleton
    // instead of ever explaining why.
    retry: (failureCount, err: any) => err?.response?.status !== 403 && failureCount < 3,
  });

  const { data: transactions } = useQuery({
    queryKey: ["credit-transactions"],
    queryFn: () => billingApi.listCreditTransactions(10),
    retry: (failureCount, err: any) => err?.response?.status !== 403 && failureCount < 3,
  });

  const portal = useMutation({
    mutationFn: () => billingApi.openPortal(),
    onSuccess: (session) => {
      window.location.href = session.url;
    },
    onError: () =>
      toast({ title: "Could not open billing portal", variant: "destructive" }),
  });

  const cancel = useMutation({
    mutationFn: () => billingApi.cancelSubscription(),
    onSuccess: () => {
      toast({ title: "Subscription will cancel at the end of the current period" });
      queryClient.invalidateQueries({ queryKey: ["billing-dashboard"] });
    },
    onError: () => toast({ title: "Could not cancel subscription", variant: "destructive" }),
  });

  const buyCredits = useMutation({
    mutationFn: (packId: string) => billingApi.checkoutCredits(packId),
    onSuccess: (session) => {
      window.location.href = session.url;
    },
    onError: () => toast({ title: "Could not start checkout", variant: "destructive" }),
  });

  const buySlot = useMutation({
    mutationFn: () => billingApi.purchaseAvatarSlot(),
    onSuccess: () => {
      toast({ title: "Avatar slot added" });
      queryClient.invalidateQueries({ queryKey: ["billing-dashboard"] });
    },
    onError: (err: any) =>
      // Was a hardcoded "no active subscription" message regardless of
      // which failure actually happened — a real card-charge failure (402)
      // and a genuinely missing subscription (400) are different problems
      // with different fixes, and showing the wrong one sends the user to
      // check the wrong thing. The backend already returns a specific
      // detail for both cases; surface that instead of guessing.
      toast({
        title: "Could not add avatar slot",
        description: err?.response?.data?.detail || "Make sure you have an active subscription with billing on file.",
        variant: "destructive",
      }),
  });

  const videosCreated = usage?.by_type?.["script_generation"]?.count ?? 0;
  const plan = dashboard?.plan ?? "free";
  const status = dashboard?.status ?? "free";

  if ((dashboardError as any)?.response?.status === 403) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-6" data-testid="billing-page">
        <div>
          <h1 className="text-2xl font-bold text-text">Billing</h1>
          <p className="text-sm text-text-dim">Your subscription, usage, and PAYG credits.</p>
        </div>
        <Card>
          <CardContent className="p-10 text-center space-y-2">
            <CreditCard className="h-8 w-8 mx-auto text-text-muted" />
            <p className="text-sm text-text">Billing isn't available in a team workspace.</p>
            <p className="text-xs text-text-muted max-w-sm mx-auto">
              You're currently viewing someone else's workspace. Switch to your own workspace
              from the switcher at the top of the page to view your billing.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6" data-testid="billing-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-text">Billing</h1>
          <p className="text-sm text-text-dim">Your subscription, usage, and PAYG credits.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => navigate("/settings/pricing")}>
            {plan === "free" ? "Upgrade" : "Change plan"}
          </Button>
          {dashboard && !dashboard.is_free_tier && (
            <Button variant="outline" onClick={() => portal.mutate()} disabled={portal.isPending}>
              Manage subscription
            </Button>
          )}
        </div>
      </div>

      {/* Subscription */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Subscription</CardTitle>
        </CardHeader>
        <CardContent>
          {dashboardLoading || !dashboard ? (
            <Skeleton className="h-16" />
          ) : (
            <div className="flex flex-wrap items-center gap-x-8 gap-y-3 text-sm">
              <div>
                <div className="text-text-muted text-xs uppercase tracking-wider">Plan</div>
                <div className="text-text font-medium">{PLAN_LABEL[plan] ?? plan}</div>
              </div>
              {dashboard.interval && (
                <div>
                  <div className="text-text-muted text-xs uppercase tracking-wider">Billing</div>
                  <div className="text-text font-medium capitalize">{dashboard.interval}ly</div>
                </div>
              )}
              {dashboard.renewal_date && (
                <div>
                  <div className="text-text-muted text-xs uppercase tracking-wider">
                    {dashboard.cancel_at_period_end ? "Ends" : "Renews"}
                  </div>
                  <div className="text-text font-medium">
                    {new Date(dashboard.renewal_date).toLocaleDateString()}
                  </div>
                </div>
              )}
              <div>
                <div className="text-text-muted text-xs uppercase tracking-wider">Status</div>
                <Badge variant={STATUS_BADGE[status ?? "free"] ?? "secondary"} className="mt-0.5">
                  {dashboard.cancel_at_period_end ? "Cancelling" : (status ?? "free")}
                </Badge>
              </div>
              {!dashboard.is_free_tier && !dashboard.cancel_at_period_end && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-danger ml-auto"
                  onClick={async () => {
                    const confirmed = await confirmAction({
                      title: "Cancel subscription?",
                      text: dashboard.renewal_date
                        ? `Your plan will stay active until ${new Date(dashboard.renewal_date).toLocaleDateString()}, then it won't renew.`
                        : "Your plan will stay active until the end of the current period, then it won't renew.",
                      confirmButtonText: "Cancel subscription",
                    });
                    if (confirmed) cancel.mutate();
                  }}
                  disabled={cancel.isPending}
                >
                  Cancel subscription
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Usage meters */}
      <div className="grid grid-cols-1 @lg:grid-cols-3 gap-4">
        <Card>
          <CardContent className="p-4 space-y-3">
            {dashboardLoading || !dashboard ? (
              <Skeleton className="h-16" />
            ) : (
              <UsageMeter
                label="Render minutes"
                used={dashboard.render_minutes.used}
                included={dashboard.render_minutes.included}
                unit="min"
              />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 space-y-3">
            {dashboardLoading || !dashboard ? (
              <Skeleton className="h-16" />
            ) : (
              <UsageMeter
                label="Live streaming"
                used={dashboard.live_stream_hours.used}
                included={dashboard.live_stream_hours.included}
                unit="hrs"
              />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 space-y-3">
            {dashboardLoading || !dashboard ? (
              <Skeleton className="h-16" />
            ) : (
              <>
                <div className="flex justify-between text-sm">
                  <span className="text-text-dim flex items-center gap-1">
                    <Users2 className="h-3.5 w-3.5" /> Avatar slots
                  </span>
                  <span className="text-text">
                    {dashboard.avatar_slots.used} / {dashboard.avatar_slots.total}
                  </span>
                </div>
                <Progress
                  value={
                    dashboard.avatar_slots.total > 0
                      ? Math.min(100, (dashboard.avatar_slots.used / dashboard.avatar_slots.total) * 100)
                      : 0
                  }
                />
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  onClick={async () => {
                    // Charges the card on file immediately, with no other
                    // confirmation step anywhere in the flow (unlike a plan
                    // switch or new subscription, which at least redirect
                    // through Stripe's own checkout/portal UI).
                    const confirmed = await confirmAction({
                      title: "Add an avatar slot?",
                      text: "This charges your card on file immediately for one additional avatar slot.",
                      confirmButtonText: "Add avatar slot",
                    });
                    if (confirmed) buySlot.mutate();
                  }}
                  disabled={buySlot.isPending || dashboard.is_free_tier}
                >
                  + Add avatar slot
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* PAYG credits */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> PAYG credits
          </CardTitle>
          <CardDescription>
            Prepaid credits cover render/stream overage or rendering with no active subscription.
            Valid 12 months from purchase.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {dashboardLoading || !dashboard ? (
            <Skeleton className="h-10" />
          ) : (
            <>
              <div className="text-2xl font-semibold text-text">
                {formatCents(dashboard.credits.balance_cents)} available
              </div>
              <div className="flex flex-wrap gap-2">
                {["credits_20", "credits_50", "credits_100"].map((packId) => (
                  <Button
                    key={packId}
                    variant="outline"
                    size="sm"
                    onClick={() => buyCredits.mutate(packId)}
                    disabled={buyCredits.isPending}
                  >
                    Buy ${packId.split("_")[1]}
                  </Button>
                ))}
              </div>
              <div className="text-xs text-text-muted">
                Auto-top-up: {dashboard.credits.auto_topup_enabled ? "on" : "off"}
                {dashboard.credits.auto_topup_enabled && dashboard.credits.auto_topup_threshold_cents
                  ? ` — tops up when balance drops below ${formatCents(dashboard.credits.auto_topup_threshold_cents)}`
                  : ""}
              </div>
              {transactions && transactions.length > 0 && (
                <div className="pt-2 space-y-1">
                  <div className="text-xs text-text-muted uppercase tracking-wider">
                    Recent activity
                  </div>
                  {transactions.map((t) => (
                    <div
                      key={t.id}
                      className="flex justify-between text-sm border-b border-border last:border-0 py-1.5"
                    >
                      <span className="text-text-dim">
                        {t.description || t.type}
                        {t.expires_at ? ` · expires ${new Date(t.expires_at).toLocaleDateString()}` : ""}
                      </span>
                      <span className={t.amount_cents >= 0 ? "text-success" : "text-text"}>
                        {t.amount_cents >= 0 ? "+" : ""}
                        {formatCents(t.amount_cents)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Legacy 30-day cost breakdown */}
      <div className="grid grid-cols-1 @md:grid-cols-3 gap-4">
        {usageLoading ? (
          <>
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </>
        ) : (
          <>
            <MetricCard label="Videos created" value={videosCreated} />
            <MetricCard label="Renders" value={usage?.render_count ?? 0} />
            <MetricCard label="Total cost" value={formatUsd(usage?.total_price)} />
          </>
        )}
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Breakdown</CardTitle>
          <CardDescription>Your usage by category (last 30 days).</CardDescription>
        </CardHeader>
        <CardContent>
          {usageLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-6" />
              <Skeleton className="h-6" />
              <Skeleton className="h-6" />
              <Skeleton className="h-6" />
            </div>
          ) : (
            <div className="space-y-1">
              <BillingRow label="Video creation" value={usage?.render_price} />
              <BillingRow label="Music" value={usage?.music_price} />
              <BillingRow label="Avatar body shots" value={usage?.body_shot_price} />
              <BillingRow label="Social publishing" value={usage?.publish_price} />
              <div className="flex justify-between pt-3 text-sm font-semibold">
                <span>Total</span>
                <span>{formatUsd(usage?.total_price)}</span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {dashboard?.is_free_tier && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <CreditCard className="h-4 w-4" /> No payment method on file
            </CardTitle>
            <CardDescription>
              Subscribe to a plan or buy PAYG credits to keep rendering once your free minutes run
              out.
            </CardDescription>
          </CardHeader>
        </Card>
      )}
    </div>
  );
}
