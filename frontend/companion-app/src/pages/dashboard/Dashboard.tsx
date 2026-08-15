import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Plus,
  Package,
  UserCircle,
  Film,
  Clapperboard,
  ArrowRight,
  Clock,
  CheckCircle2,
  Loader2,
  AlertCircle,
  Activity,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { castsApi, avatarApi, productsApi } from "@/lib/api";
import { cdnUrl } from "@/lib/cdn";
import type { Cast } from "@/lib/types";

const QUALITY_LABELS: Record<string, string> = {
  simple: "Simple",
  hd: "HD",
  hd_plus: "HD+",
};

const STATUS_DISPLAY: Record<string, { label: string; color: string; icon: typeof Clock }> = {
  draft: { label: "Draft", color: "text-gray-400", icon: Clock },
  outline_review: { label: "Outline", color: "text-blue-400", icon: Clock },
  script_review: { label: "Scripts", color: "text-blue-400", icon: Clock },
  template_select: { label: "Layout", color: "text-blue-400", icon: Clock },
  pending_payment: { label: "Payment", color: "text-yellow-400", icon: Clock },
  generating_tts: { label: "Generating Audio", color: "text-accent", icon: Loader2 },
  tts_ready: { label: "Audio Ready", color: "text-blue-400", icon: CheckCircle2 },
  generating_videos: { label: "Rendering", color: "text-accent", icon: Loader2 },
  generating: { label: "Generating", color: "text-accent", icon: Loader2 },
  generation_failed: { label: "Failed", color: "text-red-400", icon: AlertCircle },
  ready: { label: "Ready", color: "text-green-400", icon: CheckCircle2 },
  scheduled: { label: "Scheduled", color: "text-purple-400", icon: Clock },
  live: { label: "Live", color: "text-red-400", icon: Activity },
  completed: { label: "Completed", color: "text-gray-400", icon: CheckCircle2 },
};

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function DashboardPage() {
  const navigate = useNavigate();

  const { data: castsData, isLoading: castsLoading } = useQuery({
    queryKey: ["casts"],
    queryFn: () => castsApi.list(),
    refetchInterval: 10000,
  });

  const { data: avatarsData } = useQuery({
    queryKey: ["avatars"],
    queryFn: () => avatarApi.list(),
  });

  const { data: productsData } = useQuery({
    queryKey: ["products"],
    queryFn: () => productsApi.list({ per_page: 1 }),
  });

  const casts: any[] = (castsData as any)?.casts || [];
  const avatars = avatarsData?.avatars || [];
  const productCount = productsData?.total || 0;

  // Most recently edited cast
  const recentCast = casts.length > 0
    ? [...casts].sort((a, b) => new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime())[0]
    : null;

  // Render count: casts in ready status
  const renderCount = casts.filter((c: any) => c.render_status === "ready" || c.status === "ready").length;

  // Recent activity — last 8 casts as activity items
  const recentActivity = [...casts]
    .sort((a: any, b: any) => new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime())
    .slice(0, 8);

  return (
    <div className="mx-auto max-w-5xl space-y-8" data-testid="dashboard-page">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-text">Dashboard</h1>
        <p className="text-sm text-text-dim mt-1">Welcome back. Here's what's happening.</p>
      </div>

      {/* Hero — Continue where you left off */}
      {recentCast ? (
        <div className="glass-card p-6">
          <h2 className="text-sm font-medium text-white/40 uppercase tracking-wider mb-4">
            Continue where you left off
          </h2>
          <div className="flex items-center gap-5">
            {/* Avatar thumbnail */}
            <div className="w-14 h-14 rounded-full bg-white/5 border border-white/10 overflow-hidden flex items-center justify-center shrink-0">
              {recentCast.avatar?.face_ref_key ? (
                <img src={cdnUrl(recentCast.avatar.face_ref_key)} alt="" className="w-full h-full object-cover" />
              ) : (
                <UserCircle className="w-7 h-7 text-text-muted" />
              )}
            </div>

            {/* Cast info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <p className="text-lg font-semibold text-text truncate">
                  {recentCast.name || "Untitled Cast"}
                </p>
                {recentCast.version != null && recentCast.version > 1 && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-white/50 shrink-0">
                    v{recentCast.version}
                  </span>
                )}
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-white/50 shrink-0">
                  {QUALITY_LABELS[recentCast.quality] || "Simple"}
                </span>
              </div>
              <div className="flex items-center gap-3 mt-1 text-sm text-text-muted">
                {(() => {
                  const cfg = STATUS_DISPLAY[recentCast.status?.toLowerCase()] || STATUS_DISPLAY.draft;
                  const Icon = cfg.icon;
                  return (
                    <span className={`flex items-center gap-1 ${cfg.color}`}>
                      <Icon className="h-3.5 w-3.5" /> {cfg.label}
                    </span>
                  );
                })()}
                {recentCast.blocks?.length > 0 && (
                  <span>{recentCast.blocks.length} block{recentCast.blocks.length !== 1 ? "s" : ""}</span>
                )}
                <span>Last edited {timeAgo(recentCast.updated_at || recentCast.created_at)}</span>
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2 shrink-0">
              <Button
                onClick={() => navigate(`/cast-builder/${recentCast.id}`)}
                className="gap-2"
              >
                Open Cast <ArrowRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
      ) : castsLoading ? (
        <div className="glass-card p-6 flex items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
        </div>
      ) : (
        <div className="glass-card p-6 text-center">
          <Clapperboard className="mx-auto mb-3 h-10 w-10 text-text-muted" />
          <h3 className="text-lg font-semibold text-text">No casts yet</h3>
          <p className="mt-1 text-sm text-text-dim">Create your first AI-powered cast to get started</p>
          <Button onClick={() => navigate("/cast-builder/new")} className="mt-4">
            <Plus className="mr-1 h-4 w-4" /> Create Cast
          </Button>
        </div>
      )}

      {/* Quick Actions */}
      <div className="grid grid-cols-3 gap-4">
        <button
          onClick={() => navigate("/cast-builder/new")}
          className="glass-card p-4 flex items-center gap-3 hover:border-accent/30 transition-colors text-left group"
        >
          <div className="w-10 h-10 rounded-lg gradient-accent flex items-center justify-center shrink-0">
            <Plus className="h-5 w-5 text-white" />
          </div>
          <div>
            <p className="text-sm font-medium text-text group-hover:text-accent transition-colors">New Cast</p>
            <p className="text-xs text-text-muted">Create a new cast</p>
          </div>
        </button>

        <button
          onClick={() => navigate("/my-avatar/ai-avatar")}
          className="glass-card p-4 flex items-center gap-3 hover:border-accent/30 transition-colors text-left group"
        >
          <div className="w-10 h-10 rounded-lg bg-purple-600/20 flex items-center justify-center shrink-0">
            <UserCircle className="h-5 w-5 text-purple-400" />
          </div>
          <div>
            <p className="text-sm font-medium text-text group-hover:text-accent transition-colors">New Avatar</p>
            <p className="text-xs text-text-muted">Create an AI avatar</p>
          </div>
        </button>

        <button
          onClick={() => navigate("/products")}
          className="glass-card p-4 flex items-center gap-3 hover:border-accent/30 transition-colors text-left group"
        >
          <div className="w-10 h-10 rounded-lg bg-green-600/20 flex items-center justify-center shrink-0">
            <Package className="h-5 w-5 text-green-400" />
          </div>
          <div>
            <p className="text-sm font-medium text-text group-hover:text-accent transition-colors">Import Product</p>
            <p className="text-xs text-text-muted">Add from URL or TikTok</p>
          </div>
        </button>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Casts", value: casts.length, icon: Film, color: "text-accent" },
          { label: "Avatars", value: avatars.length, icon: UserCircle, color: "text-purple-400" },
          { label: "Products", value: productCount, icon: Package, color: "text-green-400" },
          { label: "Renders", value: renderCount, icon: Clapperboard, color: "text-blue-400" },
        ].map((stat) => (
          <div key={stat.label} className="glass-card p-4 text-center">
            <stat.icon className={`h-5 w-5 mx-auto mb-2 ${stat.color}`} />
            <p className="text-2xl font-bold text-text">{stat.value}</p>
            <p className="text-xs text-text-muted mt-0.5">{stat.label}</p>
          </div>
        ))}
      </div>

      {/* Recent Activity */}
      {recentActivity.length > 0 && (
        <div className="glass-card p-5">
          <h2 className="text-sm font-medium text-white/40 uppercase tracking-wider mb-4">
            Recent Activity
          </h2>
          <div className="space-y-3">
            {recentActivity.map((cast: any) => {
              const cfg = STATUS_DISPLAY[cast.status?.toLowerCase()] || STATUS_DISPLAY.draft;
              const Icon = cfg.icon;
              return (
                <button
                  key={cast.id}
                  onClick={() => navigate(`/cast-builder/${cast.id}`)}
                  className="w-full flex items-center gap-3 p-2 rounded-lg hover:bg-white/5 transition-colors text-left"
                >
                  <div className="w-8 h-8 rounded-full bg-white/5 border border-white/10 overflow-hidden flex items-center justify-center shrink-0">
                    {cast.avatar?.face_ref_key ? (
                      <img src={cdnUrl(cast.avatar.face_ref_key)} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <Film className="w-3.5 h-3.5 text-text-muted" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-text truncate">{cast.name || "Untitled Cast"}</p>
                    <p className="text-xs text-text-muted">
                      {cast.status?.toLowerCase() === "ready" ? "Render completed" :
                       cast.status?.toLowerCase() === "generating_videos" ? "Rendering in progress" :
                       cast.status?.toLowerCase() === "tts_ready" ? "Audio preview ready" :
                       cast.status?.toLowerCase() === "generation_failed" ? "Generation failed" :
                       `Status: ${cfg.label}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Icon className={`h-3.5 w-3.5 ${cfg.color}`} />
                    <span className="text-xs text-text-muted">{timeAgo(cast.updated_at || cast.created_at)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
