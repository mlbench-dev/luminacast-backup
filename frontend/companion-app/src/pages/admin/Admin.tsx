import { useQuery } from "@tanstack/react-query";
import {
  ShieldCheck,
  Radio,
  Users,
  DollarSign,
  Cpu,
  HardDrive,
  MemoryStick,
  Activity,
  AlertTriangle,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { adminApi } from "@/lib/api";
import { formatCents, formatDuration } from "@/lib/billing";

export function AdminPage() {
  const { data: dashboard, isLoading: dashLoading } = useQuery({
    queryKey: ["admin", "dashboard"],
    queryFn: adminApi.dashboard,
    refetchInterval: 10_000,
  });

  const { data: streamsData, isLoading: streamsLoading } = useQuery({
    queryKey: ["admin", "streams"],
    queryFn: adminApi.streams,
    refetchInterval: 5_000,
  });

  const { data: creatorsData, isLoading: creatorsLoading } = useQuery({
    queryKey: ["admin", "creators"],
    queryFn: adminApi.creators,
  });

  const streams = streamsData?.active_streams ?? [];
  const creators = creatorsData?.creators ?? [];
  const health = dashboard?.server_health;

  return (
    <div className="space-y-6" data-testid="admin-page">
      <div className="flex items-center gap-3">
        <ShieldCheck className="h-6 w-6 text-accent" />
        <div>
          <h1 className="text-2xl font-bold text-text">Admin Panel</h1>
          <p className="text-sm text-text-dim">Platform overview and monitoring</p>
        </div>
      </div>

      {/* Top stats */}
      <div className="grid gap-4 md:grid-cols-4">
        <StatCard
          title="Active Streams"
          value={dashLoading ? null : (dashboard?.active_streams ?? 0).toString()}
          icon={Radio}
          color="text-danger"
        />
        <StatCard
          title="Total Creators"
          value={dashLoading ? null : (dashboard?.total_creators ?? 0).toString()}
          icon={Users}
          color="text-accent"
        />
        <StatCard
          title="Revenue Today"
          value={dashLoading ? null : formatCents(dashboard?.revenue_today_cents ?? 0)}
          icon={DollarSign}
          color="text-success"
        />
        <StatCard
          title="Server Status"
          value={dashLoading ? null : health?.status ?? "unknown"}
          icon={Activity}
          color={health?.status === "healthy" ? "text-success" : "text-warning"}
        />
      </div>

      {/* Server Health */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-4 w-4" />
            Server Health
          </CardTitle>
        </CardHeader>
        <CardContent>
          {dashLoading || !health ? (
            <div className="space-y-4">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : (
            <div className="space-y-4">
              <HealthBar
                icon={Cpu}
                label="CPU"
                value={health.cpu_percent}
                warning={80}
                critical={95}
              />
              <HealthBar
                icon={MemoryStick}
                label="Memory"
                value={health.memory_percent}
                warning={85}
                critical={95}
              />
              <HealthBar
                icon={HardDrive}
                label="Disk"
                value={health.disk_percent}
                warning={80}
                critical={95}
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Active Streams */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Radio className="h-4 w-4" />
            Active Streams
            <Badge variant="live">{streams.length}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {streamsLoading ? (
            <div className="space-y-2">
              {[1, 2].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : streams.length > 0 ? (
            <div className="space-y-2">
              {streams.map((s) => (
                <div
                  key={s.session_id}
                  className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3"
                  data-testid={`admin-stream-${s.session_id}`}
                >
                  <div className="flex items-center gap-3">
                    <span className="live-indicator" />
                    <div>
                      <p className="text-sm font-medium text-text">{s.creator_email}</p>
                      <p className="text-xs text-text-muted">Cast: {s.cast_id}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-xs">
                    <Badge variant="secondary">{s.status}</Badge>
                    <span className="text-text-dim">{formatDuration(s.uptime_seconds)}</span>
                    <span className="text-text-dim">{s.viewers} viewers</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-4 text-center text-sm text-text-muted">No active streams</p>
          )}
        </CardContent>
      </Card>

      {/* All Creators */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Users className="h-4 w-4" />
            All Creators
            <Badge variant="secondary">{creators.length}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {creatorsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : creators.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="creators-table">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-text-muted">
                    <th className="pb-2">Email</th>
                    <th className="pb-2">TikTok</th>
                    <th className="pb-2">Casts</th>
                    <th className="pb-2">Streams</th>
                    <th className="pb-2">Revenue</th>
                    <th className="pb-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {creators.map((c) => (
                    <tr key={c.id} className="border-b border-border/50" data-testid={`creator-row-${c.id}`}>
                      <td className="py-2 text-text">{c.email}</td>
                      <td className="py-2 text-text-dim">
                        {c.tiktok_accounts.length === 0 ? (
                          "—"
                        ) : (
                          <span
                            title={c.tiktok_accounts
                              .map((a) => `${a.handle ? `@${a.handle}` : "unknown"} — ${a.follower_count.toLocaleString()} followers`)
                              .join("\n")}
                          >
                            {c.tiktok_accounts.map((a) => (a.handle ? `@${a.handle}` : "unknown")).join(", ")}
                            {c.tiktok_accounts.length > 1 && (
                              <span className="text-text-muted"> ({c.tiktok_accounts.length})</span>
                            )}
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-text-dim">{c.total_casts}</td>
                      <td className="py-2 text-text-dim">{c.total_streams}</td>
                      <td className="py-2 text-success">{formatCents(c.total_revenue_cents)}</td>
                      <td className="py-2">
                        <Badge variant={c.is_active ? "success" : "secondary"}>
                          {c.is_active ? "Active" : "Inactive"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="py-4 text-center text-sm text-text-muted">No creators yet</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatCard({
  title,
  value,
  icon: Icon,
  color,
}: {
  title: string;
  value: string | null;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-5">
        <Icon className={`h-8 w-8 ${color}`} />
        <div>
          <p className="text-xs text-text-muted">{title}</p>
          {value !== null ? (
            <p className="text-xl font-bold text-text">{value}</p>
          ) : (
            <Skeleton className="mt-1 h-6 w-16" />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function HealthBar({
  icon: Icon,
  label,
  value,
  warning,
  critical,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number;
  warning: number;
  critical: number;
}) {
  const color = value >= critical ? "text-danger" : value >= warning ? "text-warning" : "text-success";

  return (
    <div className="flex items-center gap-3">
      <Icon className={`h-4 w-4 ${color}`} />
      <span className="w-16 text-sm text-text-dim">{label}</span>
      <div className="flex-1">
        <Progress value={value} className="h-3" />
      </div>
      <span className={`w-12 text-right text-sm font-medium ${color}`}>{value}%</span>
      {value >= warning && (
        <AlertTriangle className={`h-4 w-4 ${color}`} />
      )}
    </div>
  );
}
