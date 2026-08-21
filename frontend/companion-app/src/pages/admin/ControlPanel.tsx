import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Loader2, Code2, BarChart3, Activity, ChevronDown, ChevronRight,
  Save, RotateCcw, Clock, CheckCircle2, XCircle, Database, Cpu,
  HardDrive, Server, Wifi, WifiOff,
} from "lucide-react";
import { adminApi } from "@/lib/api";
import { cn } from "@/lib/cn";
import { codeNameFor } from "@/lib/engineCodeNames";
import type {
  AdminPrompt, AdminPromptDetail, AdminPromptVersion,
  AdminUsageServiceEntry, AdminUsageLogEntry,
} from "@/lib/types";

type Tab = "prompts" | "usage" | "status";

const CATEGORY_LABELS: Record<string, string> = {
  llm: "LLM System Prompts",
  chat: "Chat & Classification",
  cast_generation: "Cast/Script Generation",
  image_generation: "Image Generation",
  video_generation: "Video Generation",
  voice: "Voice & Audio",
};

const SERVICE_COLORS: Record<string, string> = {
  openrouter_llm: "bg-blue-500",
  fish_speech: "bg-green-500",
  infinitetalk: "bg-purple-500",
  flux_kontext: "bg-orange-500",
  gemini_vision: "bg-yellow-500",
  bs_roformer: "bg-red-500",
  apify_scrape: "bg-cyan-500",
  pyannote: "bg-pink-500",
};

/** Map raw service key to user-friendly code name */
function displayServiceName(key: string): string {
  return codeNameFor(key);
}

export function ControlPanelPage() {
  const [tab, setTab] = useState<Tab>("prompts");

  return (
    <div className="mx-auto max-w-6xl p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-text">Control Panel</h1>
        <p className="text-sm text-text-muted mt-1">
          Prompt management, usage tracking, and system status
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: "prompts" as Tab, label: "Prompts", icon: Code2 },
          { id: "usage" as Tab, label: "Usage Tracking", icon: BarChart3 },
          { id: "status" as Tab, label: "System Status", icon: Activity },
        ]).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px",
              tab === t.id
                ? "border-accent text-accent"
                : "border-transparent text-text-muted hover:text-text hover:border-border"
            )}
          >
            <t.icon className="h-4 w-4" />
            {t.label}
          </button>
        ))}
      </div>

      {tab === "prompts" && <PromptsTab />}
      {tab === "usage" && <UsageTab />}
      {tab === "status" && <StatusTab />}
    </div>
  );
}

// ── Prompts Tab ──

function PromptsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-prompts"],
    queryFn: () => adminApi.prompts(),
  });
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  if (isLoading) return <LoadingSpinner />;

  const prompts = data?.prompts || [];
  const categories = [...new Set(prompts.map((p) => p.category))];
  const filtered = categoryFilter === "all" ? prompts : prompts.filter((p) => p.category === categoryFilter);

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-text-muted">Filter:</span>
        <button
          onClick={() => setCategoryFilter("all")}
          className={cn(
            "px-2.5 py-1 rounded-full text-xs font-medium transition-colors",
            categoryFilter === "all" ? "bg-accent text-white" : "bg-surface text-text-muted hover:bg-card"
          )}
        >
          All ({prompts.length})
        </button>
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setCategoryFilter(cat)}
            className={cn(
              "px-2.5 py-1 rounded-full text-xs font-medium transition-colors",
              categoryFilter === cat ? "bg-accent text-white" : "bg-surface text-text-muted hover:bg-card"
            )}
          >
            {CATEGORY_LABELS[cat] || cat} ({prompts.filter((p) => p.category === cat).length})
          </button>
        ))}
      </div>

      {/* Prompt table */}
      <div className="rounded-xl border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-surface">
            <tr className="border-b border-border">
              <th className="text-left px-4 py-2.5 font-medium text-text-muted text-xs">Name</th>
              <th className="text-left px-4 py-2.5 font-medium text-text-muted text-xs">Category</th>
              <th className="text-left px-4 py-2.5 font-medium text-text-muted text-xs">Version</th>
              <th className="text-left px-4 py-2.5 font-medium text-text-muted text-xs">Preview</th>
              <th className="text-right px-4 py-2.5 font-medium text-text-muted text-xs">Chars</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <PromptRow
                key={p.key}
                prompt={p}
                isExpanded={expandedKey === p.key}
                onToggle={() => setExpandedKey(expandedKey === p.key ? null : p.key)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PromptRow({ prompt, isExpanded, onToggle }: { prompt: AdminPrompt; isExpanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-border hover:bg-card/50 cursor-pointer transition-colors"
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {isExpanded ? <ChevronDown className="h-3.5 w-3.5 text-text-muted" /> : <ChevronRight className="h-3.5 w-3.5 text-text-muted" />}
            <div>
              <code className="text-xs font-mono text-accent">{prompt.key}</code>
              <div className="text-xs text-text-muted mt-0.5">{prompt.name}</div>
            </div>
          </div>
        </td>
        <td className="px-4 py-3">
          <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-accent/10 text-accent">
            {CATEGORY_LABELS[prompt.category] || prompt.category}
          </span>
        </td>
        <td className="px-4 py-3 text-xs text-text-dim">
          v{prompt.version || 0}
        </td>
        <td className="px-4 py-3 text-xs text-text-dim max-w-xs truncate">
          {prompt.system_prompt.slice(0, 100)}...
        </td>
        <td className="px-4 py-3 text-xs text-text-dim text-right">
          {prompt.character_count.toLocaleString()}
        </td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={5} className="p-0">
            <PromptEditor promptKey={prompt.key} />
          </td>
        </tr>
      )}
    </>
  );
}

function PromptEditor({ promptKey }: { promptKey: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin-prompt-detail", promptKey],
    queryFn: () => adminApi.promptDetail(promptKey),
  });

  const [editText, setEditText] = useState<string | null>(null);
  const [changeReason, setChangeReason] = useState("");
  const [showHistory, setShowHistory] = useState(false);

  const updateMutation = useMutation({
    mutationFn: (data: { system_prompt: string; change_reason: string }) =>
      adminApi.updatePrompt(promptKey, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-prompts"] });
      queryClient.invalidateQueries({ queryKey: ["admin-prompt-detail", promptKey] });
      setEditText(null);
      setChangeReason("");
    },
  });

  const revertMutation = useMutation({
    mutationFn: (version: number) => adminApi.revertPrompt(promptKey, version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-prompts"] });
      queryClient.invalidateQueries({ queryKey: ["admin-prompt-detail", promptKey] });
    },
  });

  if (isLoading || !data) return <div className="p-4"><LoadingSpinner /></div>;

  const currentText = editText ?? data.system_prompt;
  const isEditing = editText !== null;

  return (
    <div className="border-t border-border bg-bg p-4 space-y-4">
      {/* Info bar */}
      <div className="flex items-center gap-4 text-xs text-text-muted">
        <span>{data.description}</span>
        {data.used_in && <span>Used in: {data.used_in}</span>}
      </div>

      {/* Editor */}
      <textarea
        value={currentText}
        onChange={(e) => setEditText(e.target.value)}
        className="w-full h-64 rounded-lg border border-border bg-surface p-3 font-mono text-xs text-text leading-relaxed resize-y focus:outline-hidden focus:ring-1 focus:ring-accent"
      />

      {/* Save controls */}
      {isEditing && (
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Change reason (required)"
            value={changeReason}
            onChange={(e) => setChangeReason(e.target.value)}
            className="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-hidden focus:ring-1 focus:ring-accent"
          />
          <button
            onClick={() => updateMutation.mutate({ system_prompt: currentText, change_reason: changeReason })}
            disabled={!changeReason.trim() || updateMutation.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Save className="h-3.5 w-3.5" />
            {updateMutation.isPending ? "Saving..." : "Save Changes"}
          </button>
          <button
            onClick={() => { setEditText(null); setChangeReason(""); }}
            className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:bg-card"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Version history toggle */}
      <button
        onClick={() => setShowHistory(!showHistory)}
        className="flex items-center gap-1.5 text-xs text-accent hover:underline"
      >
        <Clock className="h-3 w-3" />
        Version History ({data.history?.length || 0} versions)
        {showHistory ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
      </button>

      {showHistory && data.history && data.history.length > 0 && (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {data.history.map((v: AdminPromptVersion) => (
            <div key={v.id} className="rounded-lg border border-border bg-surface p-3 text-xs">
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-text">v{v.version}</span>
                  <span className="text-text-muted">{v.changed_by}</span>
                  <span className="text-text-dim">{new Date(v.created_at).toLocaleString()}</span>
                </div>
                <button
                  onClick={() => revertMutation.mutate(v.version)}
                  disabled={revertMutation.isPending}
                  className="flex items-center gap-1 text-accent hover:underline"
                >
                  <RotateCcw className="h-3 w-3" />
                  Revert
                </button>
              </div>
              {v.change_reason && (
                <div className="text-text-muted mb-1.5">Reason: {v.change_reason}</div>
              )}
              <pre className="text-[10px] text-text-dim max-h-20 overflow-y-auto whitespace-pre-wrap font-mono bg-bg rounded p-2">
                {v.prompt_text.slice(0, 500)}{v.prompt_text.length > 500 ? "..." : ""}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Usage Tab ──

function UsageTab() {
  const { data: dailyData, isLoading: dailyLoading } = useQuery({
    queryKey: ["admin-usage-daily"],
    queryFn: () => adminApi.usageDaily(30),
  });
  const { data: serviceData, isLoading: serviceLoading } = useQuery({
    queryKey: ["admin-usage-by-service"],
    queryFn: () => adminApi.usageByService(30),
  });
  const { data: userData, isLoading: userLoading } = useQuery({
    queryKey: ["admin-usage-by-user"],
    queryFn: () => adminApi.usageByUser(30),
  });
  const { data: recentData, isLoading: recentLoading } = useQuery({
    queryKey: ["admin-usage-recent"],
    queryFn: () => adminApi.usageRecent(50),
  });

  return (
    <div className="space-y-6">
      {/* Daily chart (simple bar representation) */}
      <div className="rounded-xl border border-border bg-surface p-5">
        <h3 className="text-sm font-semibold text-text mb-4">Daily API Calls (Last 30 Days)</h3>
        {dailyLoading ? <LoadingSpinner /> : (
          <div className="space-y-1">
            {(dailyData?.daily || []).slice(0, 14).map((day) => {
              const maxCalls = Math.max(...(dailyData?.daily || []).map((d) => d.total_calls), 1);
              const pct = (day.total_calls / maxCalls) * 100;
              return (
                <div key={day.date} className="flex items-center gap-3 text-xs">
                  <span className="w-20 text-text-muted shrink-0">{day.date.slice(5)}</span>
                  <div className="flex-1 h-5 bg-bg rounded overflow-hidden flex">
                    {Object.entries(day.services).map(([svc, data]) => {
                      const svcPct = (data.calls / day.total_calls) * pct;
                      return (
                        <div
                          key={svc}
                          className={cn("h-full", SERVICE_COLORS[svc] || "bg-gray-500")}
                          style={{ width: `${svcPct}%` }}
                          title={`${displayServiceName(svc)}: ${data.calls} calls, $${(data.cost_cents / 100).toFixed(2)}`}
                        />
                      );
                    })}
                  </div>
                  <span className="w-12 text-right text-text-dim">{day.total_calls}</span>
                  <span className="w-16 text-right text-text-dim">${(day.total_cost_cents / 100).toFixed(2)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Service breakdown */}
      <div className="rounded-xl border border-border bg-surface p-5">
        <h3 className="text-sm font-semibold text-text mb-4">Cost by Service</h3>
        {serviceLoading ? <LoadingSpinner /> : (
          <div className="space-y-3">
            {/* Visual pie-like bars */}
            {(() => {
              const services = serviceData?.services || [];
              const totalCost = services.reduce((s, svc) => s + svc.total_cost_cents, 0) || 1;
              return services.map((svc: AdminUsageServiceEntry) => (
                <div key={svc.service} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <div className={cn("w-2.5 h-2.5 rounded-full", SERVICE_COLORS[svc.service] || "bg-gray-500")} />
                      <span className="text-text font-medium">{displayServiceName(svc.service)}</span>
                    </div>
                    <div className="flex items-center gap-4 text-text-muted">
                      <span>{svc.total_calls} calls</span>
                      <span>{svc.success_rate}% success</span>
                      <span className="font-medium text-text">${(svc.total_cost_cents / 100).toFixed(2)}</span>
                    </div>
                  </div>
                  <div className="h-2 bg-bg rounded-full overflow-hidden">
                    <div
                      className={cn("h-full rounded-full", SERVICE_COLORS[svc.service] || "bg-gray-500")}
                      style={{ width: `${(svc.total_cost_cents / totalCost) * 100}%` }}
                    />
                  </div>
                </div>
              ));
            })()}
          </div>
        )}
      </div>

      {/* Per-user table */}
      <div className="rounded-xl border border-border bg-surface p-5">
        <h3 className="text-sm font-semibold text-text mb-4">Usage by User</h3>
        {userLoading ? <LoadingSpinner /> : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left px-3 py-2 text-text-muted font-medium">User</th>
                <th className="text-right px-3 py-2 text-text-muted font-medium">Calls</th>
                <th className="text-right px-3 py-2 text-text-muted font-medium">Cost</th>
                <th className="text-right px-3 py-2 text-text-muted font-medium">Success Rate</th>
              </tr>
            </thead>
            <tbody>
              {(userData?.users || []).map((u) => (
                <tr key={u.user_id} className="border-b border-border/50">
                  <td className="px-3 py-2 text-text">{u.email}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{u.total_calls}</td>
                  <td className="px-3 py-2 text-right text-text-dim">${(u.total_cost_cents / 100).toFixed(2)}</td>
                  <td className="px-3 py-2 text-right">
                    <span className={cn(
                      "font-medium",
                      u.success_rate >= 95 ? "text-green-500" : u.success_rate >= 80 ? "text-yellow-500" : "text-red-500"
                    )}>
                      {u.success_rate}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Recent calls log */}
      <div className="rounded-xl border border-border bg-surface p-5">
        <h3 className="text-sm font-semibold text-text mb-4">Recent API Calls</h3>
        {recentLoading ? <LoadingSpinner /> : (
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-border">
                  <th className="text-left px-3 py-2 text-text-muted font-medium">Time</th>
                  <th className="text-left px-3 py-2 text-text-muted font-medium">Service</th>
                  <th className="text-left px-3 py-2 text-text-muted font-medium">Operation</th>
                  <th className="text-right px-3 py-2 text-text-muted font-medium">Duration</th>
                  <th className="text-center px-3 py-2 text-text-muted font-medium">Status</th>
                  <th className="text-right px-3 py-2 text-text-muted font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {(recentData?.logs || []).map((log: AdminUsageLogEntry) => (
                  <tr key={log.id} className="border-b border-border/50 hover:bg-card/30">
                    <td className="px-3 py-2 text-text-dim">
                      {log.created_at ? new Date(log.created_at).toLocaleTimeString() : "-"}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <div className={cn("w-2 h-2 rounded-full", SERVICE_COLORS[log.service] || "bg-gray-500")} />
                        <span className="text-text">{displayServiceName(log.service)}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-text-dim">{log.operation}</td>
                    <td className="px-3 py-2 text-right text-text-dim">
                      {log.duration_seconds ? `${log.duration_seconds.toFixed(1)}s` : "-"}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {log.success ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-green-500 inline" />
                      ) : (
                        <span title={log.error_message || ""}>
                          <XCircle className="h-3.5 w-3.5 text-red-500 inline" />
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right text-text-dim">
                      ${(log.cost_cents / 100).toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Status Tab ──

function StatusTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-status"],
    queryFn: () => adminApi.status(),
    refetchInterval: 15000,
  });

  if (isLoading) return <LoadingSpinner />;
  if (!data) return <div className="text-text-muted text-sm">Failed to load status</div>;

  const formatUptime = (s: number) => {
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m` : `${m}m`;
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Server Health */}
      <StatusCard title="Server" icon={Server} status={data.server.status}>
        <StatusRow label="CPU" value={`${data.server.cpu_percent}%`} bar={data.server.cpu_percent} />
        <StatusRow
          label="Memory"
          value={`${data.server.memory_used_gb}/${data.server.memory_total_gb} GB`}
          bar={data.server.memory_percent}
        />
        <StatusRow
          label="Disk"
          value={`${data.server.disk_used_gb}/${data.server.disk_total_gb} GB`}
          bar={data.server.disk_percent}
        />
        <div className="flex justify-between text-xs pt-1">
          <span className="text-text-muted">Uptime</span>
          <span className="text-text">{formatUptime(data.server.uptime_seconds)}</span>
        </div>
      </StatusCard>

      {/* GPU Server */}
      <StatusCard
        title="GPU Server"
        icon={Cpu}
        status={data.gpu_server.status}
      >
        <div className="flex justify-between text-xs">
          <span className="text-text-muted">Status</span>
          <StatusBadge status={data.gpu_server.status} />
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-text-muted">URL</span>
          <span className="text-text-dim truncate ml-4">{data.gpu_server.url || "not configured"}</span>
        </div>
      </StatusCard>

      {/* RunPod Endpoints */}
      <StatusCard title="RunPod Endpoints" icon={Wifi}>
        {Object.entries(data.runpod_endpoints).map(([name, id]) => (
          <div key={name} className="flex justify-between text-xs">
            <span className="text-text-muted">{name}</span>
            <span className={cn("font-mono", id === "not configured" ? "text-text-dim" : "text-text")}>
              {id}
            </span>
          </div>
        ))}
      </StatusCard>

      {/* R2 Storage */}
      <StatusCard
        title="R2 Storage"
        icon={HardDrive}
        status={data.r2_storage.configured ? "healthy" : "disabled"}
      >
        <div className="flex justify-between text-xs">
          <span className="text-text-muted">Configured</span>
          <StatusBadge status={data.r2_storage.configured ? "healthy" : "disabled"} />
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-text-muted">Bucket</span>
          <span className="text-text">{data.r2_storage.bucket}</span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-text-muted">Public URL</span>
          <span className="text-text-dim truncate ml-4">{data.r2_storage.public_url}</span>
        </div>
      </StatusCard>

      {/* Zernio — single platform-wide account, so a failure (e.g. its
          shared plan hitting a post-limit) affects every user until we
          notice. Shown here so admins see it's an ongoing problem without
          having to go dig through Sentry. */}
      <StatusCard
        title="Zernio (Social Publishing)"
        icon={Wifi}
        status={
          !data.zernio?.configured
            ? "disabled"
            : data.zernio.failures_24h > 0
              ? "unhealthy"
              : "healthy"
        }
      >
        <div className="flex justify-between text-xs">
          <span className="text-text-muted">Configured</span>
          <StatusBadge status={data.zernio?.configured ? "healthy" : "disabled"} />
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-text-muted">Failures (24h)</span>
          <span className={cn(data.zernio?.failures_24h > 0 ? "text-red-500 font-medium" : "text-text")}>
            {data.zernio?.failures_24h ?? 0}
          </span>
        </div>
        {data.zernio?.last_error && (
          <div className="text-[10px] text-text-dim truncate" title={data.zernio.last_error}>
            Last error: {data.zernio.last_error}
          </div>
        )}
      </StatusCard>

      {/* Database */}
      <StatusCard title="Database" icon={Database} className="md:col-span-2">
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(data.database).map(([table, count]) => (
            <div key={table} className="rounded-lg bg-bg p-3 text-center">
              <div className="text-lg font-bold text-text">{count.toLocaleString()}</div>
              <div className="text-[10px] text-text-muted">{table}</div>
            </div>
          ))}
        </div>
      </StatusCard>

      {/* Config indicators */}
      <StatusCard title="Integrations" icon={Activity} className="md:col-span-2">
        <div className="grid grid-cols-2 gap-2">
          <IntegrationRow label="Sentry" configured={data.sentry_configured} />
        </div>
      </StatusCard>
    </div>
  );
}

// ── Shared components ──

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-10">
      <Loader2 className="h-5 w-5 animate-spin text-accent" />
    </div>
  );
}

function StatusCard({
  title, icon: Icon, status, children, className,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  status?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-xl border border-border bg-surface p-5 space-y-3", className)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-semibold text-text">{title}</h3>
        </div>
        {status && <StatusBadge status={status} />}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color = status === "healthy" ? "bg-green-500/10 text-green-500"
    : status === "disabled" || status === "not configured" ? "bg-gray-500/10 text-gray-400"
    : status === "unreachable" || status === "unhealthy" ? "bg-red-500/10 text-red-500"
    : "bg-yellow-500/10 text-yellow-500";
  return (
    <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-medium", color)}>
      {status}
    </span>
  );
}

function StatusRow({ label, value, bar }: { label: string; value: string; bar: number }) {
  const barColor = bar > 90 ? "bg-red-500" : bar > 70 ? "bg-yellow-500" : "bg-green-500";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-text-muted">{label}</span>
        <span className="text-text">{value}</span>
      </div>
      <div className="h-1.5 bg-bg rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", barColor)} style={{ width: `${Math.min(bar, 100)}%` }} />
      </div>
    </div>
  );
}

function IntegrationRow({ label, configured }: { label: string; configured: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-bg px-3 py-2">
      <span className="text-xs text-text">{label}</span>
      {configured ? (
        <div className="flex items-center gap-1 text-green-500">
          <Wifi className="h-3 w-3" />
          <span className="text-[10px]">Connected</span>
        </div>
      ) : (
        <div className="flex items-center gap-1 text-text-dim">
          <WifiOff className="h-3 w-3" />
          <span className="text-[10px]">Not configured</span>
        </div>
      )}
    </div>
  );
}
