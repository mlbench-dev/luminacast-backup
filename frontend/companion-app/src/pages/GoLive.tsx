import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Radio, Plus, Trash2, Copy, Check, ExternalLink, Mail, Loader2, AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { castsApi, avatarApi, goLiveApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";

type Platform = "tiktok" | "instagram" | "youtube" | "facebook" | "twitch" | "custom";

const PLATFORMS: { value: Platform; label: string }[] = [
  { value: "tiktok", label: "TikTok" },
  { value: "instagram", label: "Instagram" },
  { value: "youtube", label: "YouTube" },
  { value: "facebook", label: "Facebook" },
  { value: "twitch", label: "Twitch" },
  { value: "custom", label: "Custom RTMP" },
];

const DURATION_OPTIONS: { value: number | null; label: string }[] = [
  { value: 60, label: "1 hour" },
  { value: 120, label: "2 hours" },
  { value: 240, label: "4 hours" },
  { value: null, label: "Until stopped" },
];

interface PlatformConfig {
  platform: Platform;
  enabled: boolean;
  stream_key: string;
  rtmp_url: string;  // only used for "custom"
}

interface PendingInvite {
  email: string;
  role: "admin" | "monitor" | "moderator";
}

/**
 * Go Live page.
 *
 * Three sections:
 *   1. Pick casts to rotate through (only ready renders are eligible).
 *   2. Configure platforms + stream keys + duration + chat reactivity.
 *   3. Invite team to monitor (optional).
 *
 * On Start, we POST /live-sessions/golive and surface the OBS browser-source
 * URL + monitor URL. The user copies the URL into OBS (or our streamer app)
 * and the stream begins.
 */
export default function GoLive() {
  const navigate = useNavigate();

  // 1. Cast picker --------------------------------------------------------
  const { data: castsData } = useQuery({
    queryKey: ["casts"],
    queryFn: () => castsApi.list(),
  });
  const allCasts = (castsData?.casts || []) as any[];
  const eligibleCasts = useMemo(
    () => allCasts.filter((c) => (c.render_status || "").toLowerCase() === "ready"),
    [allCasts],
  );
  const [selectedCastIds, setSelectedCastIds] = useState<string[]>([]);
  // Maintain insertion order so the rotation sequence matches selection order.

  // 2. Stream settings ----------------------------------------------------
  const { data: avatarsData } = useQuery({
    queryKey: ["avatars"],
    queryFn: () => avatarApi.list(),
  });
  const avatars = avatarsData?.avatars || [];
  const [avatarId, setAvatarId] = useState<string>("");
  useEffect(() => {
    if (avatars.length && !avatarId) setAvatarId(avatars[0].id);
  }, [avatars, avatarId]);

  const [platforms, setPlatforms] = useState<PlatformConfig[]>([
    { platform: "tiktok", enabled: true, stream_key: "", rtmp_url: "" },
    { platform: "instagram", enabled: false, stream_key: "", rtmp_url: "" },
    { platform: "youtube", enabled: false, stream_key: "", rtmp_url: "" },
    { platform: "facebook", enabled: false, stream_key: "", rtmp_url: "" },
  ]);
  const togglePlatform = (idx: number) => {
    setPlatforms((p) =>
      p.map((row, i) => (i === idx ? { ...row, enabled: !row.enabled } : row)),
    );
  };
  const updatePlatform = (idx: number, patch: Partial<PlatformConfig>) => {
    setPlatforms((p) => p.map((row, i) => (i === idx ? { ...row, ...patch } : row)));
  };

  const [duration, setDuration] = useState<number | null>(120);
  const [chatReactivity, setChatReactivity] = useState<"high" | "medium" | "low">("medium");
  const [productRotationMinutes, setProductRotationMinutes] = useState(10);

  // 3. Invites ------------------------------------------------------------
  const [pendingInvites, setPendingInvites] = useState<PendingInvite[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "monitor" | "moderator">("monitor");

  // Result state ----------------------------------------------------------
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<Awaited<ReturnType<typeof goLiveApi.create>> | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const enabledPlatforms = platforms.filter((p) => p.enabled && p.stream_key.trim());
  const canStart =
    avatarId && selectedCastIds.length > 0 && enabledPlatforms.length > 0 && !creating;

  const handleStart = async () => {
    if (!canStart) return;
    setCreating(true);
    try {
      const res = await goLiveApi.create({
        avatar_id: avatarId,
        title: `Live ${new Date().toLocaleDateString()}`,
        cast_selections: selectedCastIds.map((id, i) => ({
          cast_id: id,
          rotation_order: i,
        })),
        platforms: enabledPlatforms.map((p) => ({
          platform: p.platform,
          stream_key: p.stream_key.trim(),
          rtmp_url: p.platform === "custom" ? p.rtmp_url.trim() : undefined,
          enabled: true,
        })),
        duration_minutes: duration,
        chat_reactivity: chatReactivity,
        product_rotation_minutes: productRotationMinutes,
      });
      setCreated(res);

      // Send invites in parallel; non-fatal if any fail.
      await Promise.allSettled(
        pendingInvites.map((inv) =>
          goLiveApi.invite(res.session_id, { email: inv.email, role: inv.role }),
        ),
      );

      // Spawn the compositor in the background. The user sees the OBS URL
      // immediately; the compositor will flip status from `composing` → `live`.
      try {
        await goLiveApi.start(res.session_id);
      } catch (startErr: any) {
        toast({
          title: "Compositor did not start",
          description: startErr?.response?.data?.detail || startErr.message,
          variant: "warning",
        });
      }

      toast({ title: "Live session created", description: "Copy the OBS URL or use the streamer app." });
    } catch (err: any) {
      toast({
        title: "Could not create live session",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setCreating(false);
    }
  };

  const copyToClipboard = (key: string, value: string) => {
    navigator.clipboard?.writeText(value);
    setCopiedField(key);
    setTimeout(() => setCopiedField(null), 1500);
  };

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <div className="flex items-center gap-2">
        <Radio className="w-5 h-5 text-accent" />
        <h1 className="text-xl font-semibold text-white">Go Live</h1>
      </div>
      <p className="text-sm text-white/50">
        Pick the casts to rotate, paste your platform stream keys, and start.
        The server composes a single feed; your machine relays it to all
        platforms simultaneously.
      </p>

      {created ? (
        <CreatedSession
          created={created}
          onOpenMonitor={() =>
            navigate(`/live/${created.session_id}/monitor`)
          }
          copyToClipboard={copyToClipboard}
          copiedField={copiedField}
        />
      ) : (
        <>
          <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
            <h2 className="text-sm font-semibold text-white/80">1. Select casts</h2>
            <p className="text-xs text-white/40">
              Each selected cast becomes a product rotation slot. Only casts with
              a ready render are eligible.
            </p>
            {eligibleCasts.length === 0 ? (
              <div className="rounded-md border border-dashed border-white/10 p-4 text-center text-xs text-white/50">
                No casts have a ready render yet. Render a cast first, then come back here.
              </div>
            ) : (
              <ul className="space-y-2">
                {eligibleCasts.map((c) => {
                  const idx = selectedCastIds.indexOf(c.id);
                  const checked = idx >= 0;
                  return (
                    <li
                      key={c.id}
                      onClick={() =>
                        setSelectedCastIds((sel) =>
                          checked ? sel.filter((x) => x !== c.id) : [...sel, c.id],
                        )
                      }
                      className={`flex items-center gap-3 rounded-md border px-3 py-2 cursor-pointer transition ${
                        checked
                          ? "border-accent/60 bg-accent/10"
                          : "border-white/10 hover:border-white/20"
                      }`}
                    >
                      <span
                        className={`h-4 w-4 shrink-0 rounded border ${
                          checked ? "bg-accent border-accent" : "border-white/20"
                        } flex items-center justify-center`}
                      >
                        {checked && <Check className="h-3 w-3 text-white" />}
                      </span>
                      <span className="flex-1 text-sm text-white/90 truncate">
                        {c.name || c.id}
                      </span>
                      {checked && (
                        <span className="text-[10px] text-accent">
                          #{idx + 1} in rotation
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-4">
            <h2 className="text-sm font-semibold text-white/80">2. Stream settings</h2>

            {avatars.length > 0 && (
              <div className="space-y-1">
                <label className="text-xs text-white/50">Voice for live narration</label>
                <select
                  value={avatarId}
                  onChange={(e) => setAvatarId(e.target.value)}
                  className="w-full rounded-md bg-white/5 border border-white/10 px-3 py-2 text-sm text-white"
                >
                  {avatars.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name || a.id}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="space-y-2">
              <label className="text-xs text-white/50">Platforms</label>
              <ul className="space-y-2">
                {platforms.map((p, idx) => (
                  <li
                    key={p.platform + idx}
                    className={`flex items-center gap-3 rounded-md border px-3 py-2 ${
                      p.enabled ? "border-accent/40 bg-accent/[0.04]" : "border-white/10"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={p.enabled}
                      onChange={() => togglePlatform(idx)}
                      className="accent-accent"
                    />
                    <span className="w-24 shrink-0 text-sm text-white/80">
                      {PLATFORMS.find((x) => x.value === p.platform)?.label || p.platform}
                    </span>
                    <input
                      type="text"
                      placeholder={
                        p.platform === "custom" ? "rtmp://..." : "stream key"
                      }
                      value={p.platform === "custom" ? p.rtmp_url : p.stream_key}
                      onChange={(e) =>
                        updatePlatform(idx,
                          p.platform === "custom"
                            ? { rtmp_url: e.target.value }
                            : { stream_key: e.target.value },
                        )
                      }
                      disabled={!p.enabled}
                      className="flex-1 rounded-md bg-white/5 border border-white/10 px-2 py-1 text-xs text-white placeholder-white/30 disabled:opacity-50"
                    />
                    {p.platform === "custom" && p.enabled && (
                      <input
                        type="text"
                        placeholder="stream key"
                        value={p.stream_key}
                        onChange={(e) => updatePlatform(idx, { stream_key: e.target.value })}
                        className="w-40 rounded-md bg-white/5 border border-white/10 px-2 py-1 text-xs text-white placeholder-white/30"
                      />
                    )}
                  </li>
                ))}
              </ul>
              <p className="text-[10px] text-white/30">
                Stream keys never leave your browser without your approval.
                We send them to your streaming app via the live session token.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <label className="text-xs text-white/50">Duration</label>
                <select
                  value={duration === null ? "null" : String(duration)}
                  onChange={(e) =>
                    setDuration(e.target.value === "null" ? null : Number(e.target.value))
                  }
                  className="w-full rounded-md bg-white/5 border border-white/10 px-3 py-2 text-sm text-white"
                >
                  {DURATION_OPTIONS.map((opt) => (
                    <option key={String(opt.value)} value={opt.value === null ? "null" : String(opt.value)}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-white/50">Product rotation</label>
                <select
                  value={String(productRotationMinutes)}
                  onChange={(e) => setProductRotationMinutes(Number(e.target.value))}
                  className="w-full rounded-md bg-white/5 border border-white/10 px-3 py-2 text-sm text-white"
                >
                  <option value="5">Every 5 min</option>
                  <option value="10">Every 10 min</option>
                  <option value="15">Every 15 min</option>
                  <option value="20">Every 20 min</option>
                </select>
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-xs text-white/50">Chat reactivity</label>
              <div className="flex gap-2">
                {[
                  { v: "high", l: "High — respond to most" },
                  { v: "medium", l: "Medium" },
                  { v: "low", l: "Low — purchases only" },
                ].map((opt) => (
                  <button
                    key={opt.v}
                    onClick={() => setChatReactivity(opt.v as any)}
                    className={`flex-1 rounded-md px-3 py-1.5 text-xs ${
                      chatReactivity === opt.v
                        ? "bg-accent text-white"
                        : "bg-white/5 text-white/70 hover:bg-white/10"
                    }`}
                  >
                    {opt.l}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
            <h2 className="text-sm font-semibold text-white/80">3. Invite team (optional)</h2>
            <p className="text-xs text-white/40">
              Admins can override the AI mid-stream (skip product, inject a
              message, end stream). Monitors get a read-only dashboard.
            </p>

            <div className="flex items-center gap-2">
              <Mail className="h-4 w-4 text-white/40" />
              <input
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="email@example.com"
                className="flex-1 rounded-md bg-white/5 border border-white/10 px-3 py-2 text-sm text-white placeholder-white/30"
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as any)}
                className="rounded-md bg-white/5 border border-white/10 px-2 py-2 text-sm text-white"
              >
                <option value="admin">Admin</option>
                <option value="monitor">Monitor</option>
                <option value="moderator">Moderator</option>
              </select>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  if (!inviteEmail.trim()) return;
                  setPendingInvites((p) => [
                    ...p,
                    { email: inviteEmail.trim(), role: inviteRole },
                  ]);
                  setInviteEmail("");
                }}
              >
                <Plus className="h-3 w-3" /> Add
              </Button>
            </div>
            {pendingInvites.length > 0 && (
              <ul className="space-y-1.5">
                {pendingInvites.map((inv, i) => (
                  <li
                    key={inv.email + i}
                    className="flex items-center justify-between rounded-md bg-white/[0.04] px-3 py-1.5 text-xs text-white/80"
                  >
                    <span>
                      {inv.email} <span className="text-white/40">· {inv.role}</span>
                    </span>
                    <button
                      onClick={() => setPendingInvites((p) => p.filter((_, j) => j !== i))}
                      className="text-white/40 hover:text-red-400"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <Button
            onClick={handleStart}
            disabled={!canStart}
            className="w-full bg-red-500 hover:bg-red-600 text-white py-3"
          >
            {creating ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Starting…
              </>
            ) : (
              <>
                <Radio className="mr-2 h-4 w-4" /> Start live session
              </>
            )}
          </Button>
        </>
      )}
    </div>
  );
}


function CreatedSession({
  created, onOpenMonitor, copyToClipboard, copiedField,
}: {
  created: NonNullable<Awaited<ReturnType<typeof goLiveApi.create>>>;
  onOpenMonitor: () => void;
  copyToClipboard: (key: string, value: string) => void;
  copiedField: string | null;
}) {
  return (
    <div className="rounded-xl border border-accent/40 bg-accent/[0.06] p-6 space-y-5">
      <div className="flex items-center gap-2">
        <Radio className="h-5 w-5 text-accent animate-pulse" />
        <h2 className="text-base font-semibold text-white">Session ready</h2>
      </div>
      <p className="text-sm text-white/70">
        Paste the OBS URL into a Browser Source, or open our streamer app and
        scan the session code. Then start the stream from your platforms.
      </p>

      <div className="space-y-3">
        <UrlField
          label="OBS Browser Source URL"
          value={created.obs_browser_source_url}
          onCopy={() => copyToClipboard("obs", created.obs_browser_source_url)}
          copied={copiedField === "obs"}
        />
        <UrlField
          label="HLS playback URL (preview)"
          value={created.hls_play_url}
          onCopy={() => copyToClipboard("hls", created.hls_play_url)}
          copied={copiedField === "hls"}
        />
        <UrlField
          label="RTMP play URL (for streamer apps)"
          value={created.rtmp_play_url}
          onCopy={() => copyToClipboard("rtmp", created.rtmp_play_url)}
          copied={copiedField === "rtmp"}
        />
      </div>

      <div className="flex gap-2">
        <Button onClick={onOpenMonitor} className="flex-1 bg-accent hover:bg-accent/90">
          Open Monitor Dashboard
        </Button>
        <Button
          variant="outline"
          onClick={() => window.open(created.monitor_url, "_blank")}
        >
          <ExternalLink className="h-3.5 w-3.5" /> External
        </Button>
      </div>

      <div className="flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 p-3 text-xs text-yellow-200">
        <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
        <p>
          The compositor warms up over the next ~30 seconds. The monitor will
          show the live preview once ffmpeg connects to the relay.
        </p>
      </div>
    </div>
  );
}


function UrlField({
  label, value, onCopy, copied,
}: { label: string; value: string; onCopy: () => void; copied: boolean }) {
  return (
    <div className="space-y-1">
      <label className="text-xs text-white/50">{label}</label>
      <div className="flex gap-2">
        <input
          readOnly
          value={value}
          className="flex-1 rounded-md bg-black/40 border border-white/10 px-3 py-2 text-xs text-white/80 font-mono"
        />
        <Button size="sm" variant="outline" onClick={onCopy} className="shrink-0">
          {copied ? <Check className="h-3.5 w-3.5 text-green-400" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      </div>
    </div>
  );
}
