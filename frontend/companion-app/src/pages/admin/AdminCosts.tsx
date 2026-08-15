import { useQuery } from "@tanstack/react-query";
import { BarChart3, AlertTriangle } from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";

type Totals = {
  provider_cost: number;
  user_revenue: number;
  gross_margin: number;
  gross_margin_pct: number;
  fixed_costs: number;
  net_margin: number;
  total_events: number;
};

type ByProvider = { provider: string; cost: number; count: number };
type ByEventType = { type: string; cost: number; count: number; avg: number };
type ByUser = { user_id: string; cost: number; revenue: number; events: number };
type DailyTrend = { date: string; cost: number; revenue: number; events: number };

type Overview = {
  period_days: number;
  totals: Totals;
  by_provider: ByProvider[];
  by_event_type: ByEventType[];
  by_user: ByUser[];
  daily_trend: DailyTrend[];
  markup_multiplier: number;
};

function MetricCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  tone?: "neutral" | "good" | "bad" | "info";
}) {
  const toneClass = {
    neutral: "text-text",
    good: "text-success",
    bad: "text-danger",
    info: "text-info",
  }[tone];
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-xs text-text-muted uppercase tracking-wider">{label}</div>
        <div className={cn("mt-1 text-xl font-semibold", toneClass)}>{value}</div>
      </CardContent>
    </Card>
  );
}

function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "$0.00";
  return `$${n.toFixed(2)}`;
}

export function AdminCostsPage() {
  const { data, isLoading, isError } = useQuery<Overview>({
    queryKey: ["admin-costs", "overview", 30],
    queryFn: () => api.get<Overview>("/admin/costs/overview?days=30").then((r) => r.data),
  });

  if (isLoading) {
    return (
      <div className="space-y-6" data-testid="admin-costs-loading">
        <Skeleton className="h-10 w-72" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
        <Skeleton className="h-32" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-4" data-testid="admin-costs-error">
        <h1 className="text-2xl font-bold text-text">Cost Dashboard</h1>
        <Card>
          <CardContent className="p-6 flex items-center gap-3 text-text-dim">
            <AlertTriangle className="h-5 w-5 text-warning" />
            <span>Failed to load cost data. Try refreshing the page.</span>
          </CardContent>
        </Card>
      </div>
    );
  }

  const { totals, by_provider, by_event_type, by_user, daily_trend } = data;
  const hasData = totals.total_events > 0;

  return (
    <div className="space-y-6" data-testid="admin-costs-page">
      <div>
        <h1 className="text-2xl font-bold text-text">Platform Economics</h1>
        <p className="text-sm text-text-dim">Last {data.period_days} days · {totals.total_events} events</p>
      </div>

      {!hasData && (
        <Card>
          <CardContent className="p-6 text-sm text-text-dim">
            No usage data yet. Once renders and other billable actions run, metrics will appear here.
          </CardContent>
        </Card>
      )}

      {/* Top-line metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard label="Revenue" value={formatUsd(totals.user_revenue)} tone="good" />
        <MetricCard label="Provider Costs" value={formatUsd(totals.provider_cost)} tone="bad" />
        <MetricCard
          label="Gross Margin"
          value={`${formatUsd(totals.gross_margin)} (${totals.gross_margin_pct}%)`}
          tone="info"
        />
        <MetricCard
          label="Net (after fixed)"
          value={formatUsd(totals.net_margin)}
          tone={totals.net_margin >= 0 ? "good" : "bad"}
        />
      </div>

      {/* Cost by provider / event type */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Cost by Provider</CardTitle>
          </CardHeader>
          <CardContent>
            {by_provider.length === 0 ? (
              <div className="text-xs text-text-muted">No provider activity in this window.</div>
            ) : (
              <div className="space-y-1.5">
                {by_provider.map((p) => (
                  <div
                    key={p.provider}
                    className="flex justify-between py-1.5 text-sm border-b border-border last:border-0"
                  >
                    <span className="text-text-dim">{p.provider}</span>
                    <span className="text-text">
                      {formatUsd(p.cost)} <span className="text-text-muted">({p.count})</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Cost by Action</CardTitle>
          </CardHeader>
          <CardContent>
            {by_event_type.length === 0 ? (
              <div className="text-xs text-text-muted">No actions logged in this window.</div>
            ) : (
              <div className="space-y-1.5">
                {by_event_type.map((t) => (
                  <div
                    key={t.type}
                    className="flex justify-between py-1.5 text-sm border-b border-border last:border-0"
                  >
                    <span className="text-text-dim">{t.type}</span>
                    <span className="text-text">
                      {formatUsd(t.cost)}{" "}
                      <span className="text-text-muted">avg ${t.avg.toFixed(4)}</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Daily trend */}
      <Card>
        <CardHeader className="flex flex-row items-center gap-2 pb-2">
          <BarChart3 className="h-4 w-4 text-accent" />
          <CardTitle className="text-sm">Daily Trend (Revenue)</CardTitle>
        </CardHeader>
        <CardContent>
          {daily_trend.length === 0 ? (
            <div className="text-xs text-text-muted">No daily data yet.</div>
          ) : (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={daily_trend}>
                  <CartesianGrid stroke="#2A2A3A" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: "#8B89A0", fontSize: 11 }}
                    stroke="#2A2A3A"
                  />
                  <YAxis tick={{ fill: "#8B89A0", fontSize: 11 }} stroke="#2A2A3A" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1c1c28",
                      border: "1px solid #2a2a3a",
                      borderRadius: "0.5rem",
                      fontSize: 12,
                    }}
                  />
                  <Bar dataKey="revenue" fill="#5EA88A" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="cost" fill="#B05656" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Top users */}
      {by_user.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Top Users by Cost</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1.5">
              {by_user.slice(0, 10).map((u) => (
                <div
                  key={u.user_id}
                  className="flex justify-between py-1.5 text-sm border-b border-border last:border-0"
                >
                  <span className="font-mono text-xs text-text-dim">
                    {(u.user_id || "anon").slice(0, 12)}
                  </span>
                  <span className="text-text">
                    {formatUsd(u.cost)}{" "}
                    <span className="text-text-muted">/ rev {formatUsd(u.revenue)} · {u.events} ev</span>
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Fixed costs reminder */}
      <div className="text-xs text-text-muted">
        Fixed monthly: VPS €50 + Mubert $199 ≈ ${totals.fixed_costs.toFixed(2)} over the period.
        Markup multiplier: {data.markup_multiplier}×.
      </div>
    </div>
  );
}
