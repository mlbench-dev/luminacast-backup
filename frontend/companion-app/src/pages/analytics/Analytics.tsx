import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Clock,
  Eye,
  ShoppingBag,
  DollarSign,
  TrendingUp,
  AlertTriangle,
  Filter,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { analyticsApi } from "@/lib/api";
import { formatDollars, formatMinutes, formatCents } from "@/lib/billing";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  Legend,
} from "recharts";
import { cn } from "@/lib/cn";

export function AnalyticsPage() {
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  const { data: sessionsData, isLoading: sessionsLoading } = useQuery({
    queryKey: ["analytics", "sessions"],
    queryFn: analyticsApi.sessions,
  });

  const { data: variantsData, isLoading: variantsLoading } = useQuery({
    queryKey: ["analytics", "variants"],
    queryFn: analyticsApi.variants,
  });

  const sessions = sessionsData?.sessions ?? [];
  const variants = variantsData?.variants ?? [];

  // Engagement timeline from sessions
  const timelineData = sessions.slice(-15).map((s) => ({
    date: new Date(s.started_at).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    viewers: s.peak_viewers,
    purchases: s.total_purchases,
    gmv: s.total_gmv,
    duration: Math.round(s.duration_minutes),
  }));

  // Per-product variant performance
  const productPerformance = variants.reduce<Record<string, { name: string; totalPlayed: number; totalPurchases: number; avgScore: number; count: number }>>((acc, v) => {
    const key = v.product_name || "General";
    if (!acc[key]) acc[key] = { name: key, totalPlayed: 0, totalPurchases: 0, avgScore: 0, count: 0 };
    acc[key].totalPlayed += v.times_played;
    acc[key].totalPurchases += v.purchases_during;
    acc[key].avgScore += v.performance_score ?? 0;
    acc[key].count += 1;
    return acc;
  }, {});

  const productChartData = Object.values(productPerformance).map((p) => ({
    name: p.name.length > 15 ? p.name.slice(0, 15) + "..." : p.name,
    purchases: p.totalPurchases,
    plays: p.totalPlayed,
    score: p.count > 0 ? +(p.avgScore / p.count).toFixed(3) : 0,
  }));

  const isLoading = sessionsLoading || variantsLoading;

  return (
    <div className="space-y-6" data-testid="analytics-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">Analytics</h1>
          <p className="text-sm text-text-dim">Per-Cast performance and variant insights</p>
        </div>
        <Badge variant="secondary">
          {sessions.length} sessions
        </Badge>
      </div>

      {/* Engagement Timeline Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TrendingUp className="h-4 w-4" />
            Engagement Timeline
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : timelineData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={timelineData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2A2A3A" />
                <XAxis dataKey="date" tick={{ fill: "#8B89A0", fontSize: 12 }} stroke="#2A2A3A" />
                <YAxis tick={{ fill: "#8B89A0", fontSize: 12 }} stroke="#2A2A3A" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1C1C28",
                    border: "1px solid #2A2A3A",
                    borderRadius: "8px",
                    color: "#E8E6F0",
                  }}
                />
                <Legend />
                <Line type="monotone" dataKey="viewers" stroke="#7A9DBF" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="purchases" stroke="#5EA88A" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="duration" stroke="#B8933A" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-64 items-center justify-center text-text-muted">
              No session data yet.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Product Breakdown */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BarChart3 className="h-4 w-4" />
            Per-Product Performance
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : productChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={productChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2A2A3A" />
                <XAxis dataKey="name" tick={{ fill: "#8B89A0", fontSize: 11 }} stroke="#2A2A3A" />
                <YAxis tick={{ fill: "#8B89A0", fontSize: 12 }} stroke="#2A2A3A" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1C1C28",
                    border: "1px solid #2A2A3A",
                    borderRadius: "8px",
                    color: "#E8E6F0",
                  }}
                />
                <Bar dataKey="purchases" fill="#5EA88A" radius={[4, 4, 0, 0]} />
                <Bar dataKey="plays" fill="#8B82C0" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-64 items-center justify-center text-text-muted">
              No variant data yet.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Variant Performance Scores */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Variant Performance Scores</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : variants.length > 0 ? (
            <div className="space-y-2">
              {variants.slice(0, 20).map((v) => (
                <div
                  key={v.variant_id}
                  className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3"
                  data-testid={`variant-row-${v.variant_id}`}
                >
                  <div className="flex items-center gap-3">
                    <Badge variant="secondary">{v.block_type.replace("_", " ")}</Badge>
                    <span className="text-sm text-text-dim">{v.product_name || "General"}</span>
                    {v.script_preview && (
                      <span className="max-w-xs truncate text-xs text-text-muted">{v.script_preview}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-4 text-xs">
                    <span className="text-text-muted">{v.times_played} plays</span>
                    <span className="text-success">{v.purchases_during} purchases</span>
                    <Badge variant={v.performance_score && v.performance_score > 0.1 ? "success" : "secondary"}>
                      {v.performance_score?.toFixed(3) ?? "N/A"}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-8 text-center text-sm text-text-muted">
              No variant performance data yet.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Session List */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Filter className="h-4 w-4" />
            Session History
          </CardTitle>
        </CardHeader>
        <CardContent>
          {sessionsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full" />)}
            </div>
          ) : sessions.length > 0 ? (
            <div className="space-y-2">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={cn(
                    "flex items-center justify-between rounded-lg border bg-surface px-4 py-3 transition-colors hover:bg-card",
                    selectedSessionId === s.id ? "border-accent" : "border-border"
                  )}
                  onClick={() => setSelectedSessionId(s.id)}
                  data-testid={`session-row-${s.id}`}
                >
                  <div>
                    <p className="text-sm font-medium text-text">
                      {new Date(s.started_at).toLocaleDateString()} — {new Date(s.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </p>
                    <p className="text-xs text-text-muted">
                      Cast: {s.cast_id} — {formatMinutes(s.duration_minutes)}
                    </p>
                  </div>
                  <div className="flex items-center gap-4 text-xs">
                    <span className="flex items-center gap-1 text-text-dim">
                      <Eye className="h-3 w-3" />
                      {s.peak_viewers}
                    </span>
                    <span className="flex items-center gap-1 text-success">
                      <ShoppingBag className="h-3 w-3" />
                      {s.total_purchases}
                    </span>
                    <span className="flex items-center gap-1 text-text">
                      <DollarSign className="h-3 w-3" />
                      {formatDollars(s.total_gmv)}
                    </span>
                    {s.afk_events > 0 && (
                      <span className="flex items-center gap-1 text-warning">
                        <AlertTriangle className="h-3 w-3" />
                        {s.afk_events} AFK
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-8 text-center text-sm text-text-muted">
              No sessions yet. Go live to start collecting analytics.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
