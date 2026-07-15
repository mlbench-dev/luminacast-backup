import { useQuery } from "@tanstack/react-query";
import { CreditCard } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";

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

function BillingRow({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="flex justify-between py-2 text-sm border-b border-border last:border-0">
      <span className="text-text-dim">{label}</span>
      <span className="text-text">{formatUsd(value)}</span>
    </div>
  );
}

export function BillingPage() {
  const { data, isLoading } = useQuery<UsageSummary>({
    queryKey: ["my-usage", 30],
    queryFn: () => api.get<UsageSummary>("/usage/summary?days=30").then((r) => r.data),
  });

  // Distinct casts approximated by the count of script-generation events —
  // one cast = one script. This is a friendly headline number, not an exact
  // cast count (a cast can have multiple revisions). Good enough for the
  // /billing top-line; the breakdown below shows the exact spend.
  const videosCreated = data?.by_type?.["script_generation"]?.count ?? 0;

  return (
    <div className="space-y-6" data-testid="billing-page">
      <div>
        <h1 className="text-2xl font-bold text-text">Billing</h1>
        <p className="text-sm text-text-dim">Your usage over the last 30 days.</p>
      </div>

      {/* Top-line metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {isLoading ? (
          <>
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </>
        ) : (
          <>
            <MetricCard label="Videos created" value={videosCreated} />
            <MetricCard label="Renders" value={data?.render_count ?? 0} />
            <MetricCard label="Total cost" value={formatUsd(data?.total_price)} />
          </>
        )}
      </div>

      {/* Breakdown */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Breakdown</CardTitle>
          <CardDescription>Your usage by category.</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-6" />
              <Skeleton className="h-6" />
              <Skeleton className="h-6" />
              <Skeleton className="h-6" />
            </div>
          ) : (
            <div className="space-y-1">
              <BillingRow label="Video creation" value={data?.render_price} />
              <BillingRow label="Music" value={data?.music_price} />
              <BillingRow label="Avatar body shots" value={data?.body_shot_price} />
              <BillingRow label="Social publishing" value={data?.publish_price} />
              <div className="flex justify-between pt-3 text-sm font-semibold">
                <span>Total</span>
                <span>{formatUsd(data?.total_price)}</span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Stripe portal placeholder */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Payment methods</CardTitle>
          <CardDescription>
            Payment processing is not yet enabled. Once subscriptions launch, manage cards and
            invoices from here.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-sm text-text-muted">
            <CreditCard className="h-4 w-4" />
            <span>Stripe portal link coming soon.</span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
