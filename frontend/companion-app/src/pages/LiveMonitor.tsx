import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Radio, Eye, ShoppingCart, MessageSquare, TrendingUp, Clock,
  SkipForward, Send, Pause, Play, Square, Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { goLiveApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";

/**
 * Live Monitor dashboard.
 *
 * Shows:
 *   - Stream health (bitrate, dropped frames, status)
 *   - Live metrics (viewers, purchases, AOV, comments)
 *   - Current product + time on it
 *   - Event log (recent 30 events)
 *   - Admin controls (skip product, inject message, pause AI, end stream)
 *
 * Polls /live-sessions/<id>/golive-status every 3 seconds.
 */
export default function LiveMonitor() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const sid = sessionId || "";
  const [injectText, setInjectText] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const { data, refetch } = useQuery({
    queryKey: ["live-status", sid],
    queryFn: () => goLiveApi.status(sid),
    enabled: !!sid,
    refetchInterval: 3000,
  });

  if (!sid) return null;

  const fire = async (
    action:
      | "skip_product" | "inject_message" | "pause_reactions"
      | "resume_reactions" | "end_stream" | "mute_music" | "unmute_music",
    text?: string,
  ) => {
    setBusyAction(action);
    try {
      await goLiveApi.override(sid, { action, text });
      toast({ title: "Sent", description: action.replace("_", " ") });
      refetch();
      if (action === "inject_message") setInjectText("");
    } catch (err: any) {
      toast({
        title: "Override failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setBusyAction(null);
    }
  };

  const status = data?.status || "...";
  const isLive = status === "live" || status === "composing";

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-5">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Radio
            className={`h-5 w-5 ${isLive ? "text-red-500 animate-pulse" : "text-white/40"}`}
          />
          <h1 className="text-xl font-semibold text-white">{data?.title || "Live"}</h1>
          <span
            className={`text-xs px-2 py-0.5 rounded-full uppercase ${
              isLive
                ? "bg-red-500/20 text-red-300"
                : "bg-white/10 text-white/60"
            }`}
          >
            {status}
          </span>
        </div>
        <div className="text-xs text-white/40">
          {data?.started_at && <>Started {new Date(data.started_at).toLocaleTimeString()}</>}
        </div>
      </header>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Metric icon={<Eye className="h-4 w-4" />} label="Viewers"
          value={data?.total_viewers ?? 0}
          sub={data?.peak_viewers ? `peak ${data.peak_viewers}` : undefined} />
        <Metric icon={<ShoppingCart className="h-4 w-4" />} label="Purchases"
          value={data?.total_purchases ?? 0}
          sub={
            data?.total_revenue_cents
              ? `$${(data.total_revenue_cents / 100).toFixed(2)}`
              : undefined
          } />
        <Metric icon={<MessageSquare className="h-4 w-4" />} label="Comments"
          value={data?.total_comments ?? 0} />
        <Metric icon={<TrendingUp className="h-4 w-4" />} label="Bitrate"
          value={`${data?.last_bitrate_kbps ?? 0} kbps`}
          sub={data?.last_dropped_frames ? `${data.last_dropped_frames} dropped` : "0 dropped"} />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <section className="lg:col-span-2 rounded-xl border border-white/10 bg-white/[0.03] p-4">
          <h2 className="text-sm font-semibold text-white/80 mb-2 flex items-center gap-2">
            <Clock className="h-3.5 w-3.5" /> Event log
          </h2>
          {!data?.recent_events?.length ? (
            <p className="text-xs text-white/40">No events yet.</p>
          ) : (
            <ul className="space-y-1.5 max-h-[400px] overflow-y-auto pr-1">
              {data.recent_events.map((e: any) => (
                <li key={e.id} className="text-xs text-white/70 flex gap-2">
                  <span className="text-white/30 shrink-0 w-20 font-mono">
                    {new Date(e.created_at).toLocaleTimeString()}
                  </span>
                  <span className="font-mono text-accent shrink-0 w-32 truncate">
                    {e.type}
                  </span>
                  <span className="flex-1 text-white/50 truncate">
                    {summarizeEventData(e.data)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-3">
          <h2 className="text-sm font-semibold text-white/80">Admin controls</h2>
          <Button
            variant="outline"
            className="w-full justify-start"
            onClick={() => fire("skip_product")}
            disabled={busyAction !== null}
          >
            {busyAction === "skip_product" ? (
              <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
            ) : (
              <SkipForward className="h-3.5 w-3.5 mr-2" />
            )}
            Skip to next product
          </Button>

          <div className="space-y-1">
            <label className="text-[10px] text-white/40 uppercase">Inject message</label>
            <textarea
              value={injectText}
              onChange={(e) => setInjectText(e.target.value)}
              rows={2}
              placeholder="The next 10 minutes — free shipping!"
              className="w-full rounded-md bg-white/5 border border-white/10 px-2 py-1.5 text-xs text-white placeholder-white/30"
            />
            <Button
              size="sm"
              variant="outline"
              className="w-full"
              disabled={!injectText.trim() || busyAction !== null}
              onClick={() => fire("inject_message", injectText.trim())}
            >
              {busyAction === "inject_message" ? (
                <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5 mr-2" />
              )}
              Send to AI host
            </Button>
          </div>

          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" disabled={busyAction !== null}
              onClick={() => fire("pause_reactions")}>
              <Pause className="h-3.5 w-3.5 mr-2" /> Pause AI
            </Button>
            <Button variant="outline" className="flex-1" disabled={busyAction !== null}
              onClick={() => fire("resume_reactions")}>
              <Play className="h-3.5 w-3.5 mr-2" /> Resume
            </Button>
          </div>

          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" disabled={busyAction !== null}
              onClick={() => fire("mute_music")}>
              Mute music
            </Button>
            <Button variant="outline" className="flex-1" disabled={busyAction !== null}
              onClick={() => fire("unmute_music")}>
              Unmute
            </Button>
          </div>

          <Button
            variant="destructive"
            className="w-full"
            disabled={busyAction !== null}
            onClick={() => {
              if (window.confirm("End this live session now?")) fire("end_stream");
            }}
          >
            {busyAction === "end_stream" ? (
              <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
            ) : (
              <Square className="h-3.5 w-3.5 mr-2" />
            )}
            End stream
          </Button>
        </section>
      </div>
    </div>
  );
}


function Metric({
  icon, label, value, sub,
}: { icon: React.ReactNode; label: string; value: number | string; sub?: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-center gap-2 text-xs text-white/40">{icon}{label}</div>
      <div className="mt-1 text-2xl font-semibold text-white">{value}</div>
      {sub && <div className="text-[10px] text-white/40 mt-0.5">{sub}</div>}
    </div>
  );
}


/** Best-effort one-line summary of event payloads for the timeline. */
function summarizeEventData(data: any): string {
  if (!data || typeof data !== "object") return "";
  const keys = ["text", "by", "email", "role", "product_name", "product_id", "username", "amount"];
  const parts: string[] = [];
  for (const k of keys) {
    if (data[k]) parts.push(`${k}: ${truncate(String(data[k]), 50)}`);
  }
  if (!parts.length) {
    try {
      return truncate(JSON.stringify(data), 80);
    } catch {
      return "";
    }
  }
  return parts.join(" · ");
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
