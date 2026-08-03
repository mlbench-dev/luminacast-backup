import { useState, useCallback, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Radio,
  Check,
  X,
  AlertTriangle,
  MoreVertical,
  Loader2,
  Store,
} from "lucide-react";

// Platforms that publish to a storefront / affiliate marketplace rather
// than a pure social feed. Carries an amber "Seller / Affiliate" badge
// per spec so users can tell their storefront connections from their
// social ones at a glance. Adding new seller platforms? Append them
// here and the badge picks them up automatically.
const SELLER_PLATFORMS = new Set(["tiktok_shop", "amazon", "shopify"]);
import { Button } from "@/components/ui/button";
import { socialApi, type SocialChannel } from "@/lib/api";
import { cdnUrl } from "@/lib/cdn";
import { cn } from "@/lib/cn";
import { toast } from "@/hooks/useToast";
import { PlatformIcon, PLATFORMS, platformLabel } from "@/components/distribute/PlatformIcon";
import { oneLineSummary } from "@/lib/oneLineSummary";

/**
 * My Channels — unified page for every connected publishing destination.
 * Social accounts (TikTok / Instagram / YouTube / etc) and seller /
 * affiliate storefronts (TikTok Shop / Amazon / Shopify) live in one
 * grid. One-time setup, kept separate from the Publish hub which is
 * the daily work surface.
 *
 * Mounted at /channels. Old paths /settings/social-channels and
 * /seller-channels redirect here for bookmarks (see App.tsx).
 *
 * The legacy creator-scout page at /settings/channels (TikTok-Shop
 * indexing for voice cloning) is kept reachable by URL but no longer
 * has a sidebar nav item.
 */
export default function SocialChannelsPage() {
  const queryClient = useQueryClient();
  const [connectOpen, setConnectOpen] = useState(false);

  const { data: channels = [], isLoading, refetch } = useQuery({
    queryKey: ["social-channels"],
    queryFn: () => socialApi.listChannels(),
  });

  // Listen for the OAuth-callback popup posting a message back. Same
  // postMessage protocol the Publish page uses.
  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      if (typeof ev.data !== "object" || ev.data === null) return;
      if ((ev.data as any).type !== "zernio-connected") return;
      // "zernio-connected" is just the message channel's name — it fires
      // for a failed/declined OAuth too, with the real outcome carried in
      // status/error. Ignoring those meant every attempt (including a
      // declined authorization) showed a false "Channel connected" toast.
      const { platform, status, error } = ev.data as any;
      if (error || status !== "ok") {
        // Zernio's real explanation (from the connect-error lookup) can
        // read like a support article — fine on the callback popup, which
        // has room, but needs collapsing to fit a toast.
        const description = error
          ? oneLineSummary(error)
          : `Failed to connect ${platformLabel(platform)}.`;
        toast({
          title: "Could not connect",
          description,
          variant: "destructive",
        });
        return;
      }
      queryClient.invalidateQueries({ queryKey: ["social-channels"] });
      toast({
        title: "Channel connected",
        description: `${platformLabel(platform)} ready to publish.`,
        variant: "success",
      });
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [queryClient]);

  return (
    <div className="max-w-3xl mx-auto px-6 py-6 space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">My Channels</h1>
          <p className="text-xs text-white/40 mt-0.5">
            Connect your social accounts and storefronts. Videos publish to selected channels.
          </p>
        </div>
        <Button onClick={() => setConnectOpen(true)} className="bg-accent hover:bg-accent/90">
          <Plus className="w-4 h-4 mr-1.5" /> Connect Channel
        </Button>
      </header>

      {isLoading ? (
        <div className="flex items-center justify-center py-10 text-white/40 text-sm">
          <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading…
        </div>
      ) : channels.length === 0 ? (
        <EmptyState onConnect={() => setConnectOpen(true)} />
      ) : (
        <ul className="space-y-3">
          {channels.map((c) => (
            <ChannelCard key={c.id} channel={c} onChanged={refetch} />
          ))}
        </ul>
      )}

      {connectOpen && (
        <ConnectChannelModal onClose={() => setConnectOpen(false)} />
      )}
    </div>
  );
}

function EmptyState({ onConnect }: { onConnect: () => void }) {
  return (
    <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
      <Radio className="w-9 h-9 mx-auto mb-3 opacity-30" />
      <p className="text-sm text-white/70">No channels connected</p>
      <p className="text-xs text-white/40 mt-1 max-w-md mx-auto">
        Connect TikTok, Instagram, YouTube, or any of the supported platforms to start publishing your casts.
      </p>
      <Button onClick={onConnect} className="mt-4 bg-accent hover:bg-accent/90">
        <Plus className="w-4 h-4 mr-1.5" /> Connect Channel
      </Button>
    </div>
  );
}

function ChannelCard({
  channel,
  onChanged,
}: {
  channel: SocialChannel;
  onChanged: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  const disconnect = useCallback(async () => {
    if (!window.confirm(`Disconnect ${channel.handle || channel.platform}?`)) return;
    try {
      await socialApi.disconnectChannel(channel.id);
      toast({ title: "Channel disconnected" });
      onChanged();
    } catch (err: any) {
      toast({
        title: "Could not disconnect",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    }
  }, [channel, onChanged]);

  const reconnect = useCallback(async () => {
    try {
      // Without an explicit redirect_uri the backend falls back to a
      // hardcoded production callback URL, which breaks the OAuth
      // round-trip on any other origin (localhost, staging).
      const redirectUri = `${window.location.origin}/integrations/zernio/callback`;
      const res = await socialApi.connectPlatform(channel.platform, redirectUri);
      const w = 540, h = 720;
      const left = window.screenX + Math.max(0, (window.outerWidth - w) / 2);
      const top = window.screenY + Math.max(0, (window.outerHeight - h) / 2);
      window.open(
        res.auth_url,
        "zernio_connect",
        `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`,
      );
    } catch (err: any) {
      toast({
        title: "Reconnect failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    }
  }, [channel]);

  const avatarUrl =
    channel.primary_avatar?.face_image_url ||
    (channel.primary_avatar?.face_ref_key
      ? cdnUrl(channel.primary_avatar.face_ref_key)
      : null);

  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.04] p-4">
      <div className="flex items-center gap-4">
        <div className="relative shrink-0">
          <PlatformIcon platform={channel.platform} className="w-10 h-10" />
          <div
            className={cn(
              "absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-[#0f0f1a]",
              channel.status === "active" && "bg-green-500",
              channel.status === "token_expired" && "bg-red-500",
              channel.status === "disconnected" && "bg-white/30",
            )}
          />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-white truncate">
              {channel.handle || channel.display_name || platformLabel(channel.platform)}
            </span>
            {SELLER_PLATFORMS.has(channel.platform) && (
              <span className="flex items-center gap-1 text-[10px] px-2 py-0.5 bg-amber-400/10 text-amber-400/80 rounded-full">
                <Store className="w-3 h-3" /> Seller / Affiliate
              </span>
            )}
            {channel.status === "token_expired" && (
              <span className="text-[10px] px-2 py-0.5 bg-red-500/20 text-red-300 rounded-full">
                Reconnect needed
              </span>
            )}
          </div>
          <div className="text-[11px] text-white/40 truncate">
            {channel.display_name && channel.display_name !== channel.handle
              ? `${channel.display_name} · `
              : ""}
            {formatNumber(channel.follower_count)} followers
            {channel.connected_at && (
              <> · Connected {formatRelativeDate(new Date(channel.connected_at))}</>
            )}
          </div>
        </div>

        {/* Primary avatar */}
        <div className="text-right hidden sm:block">
          {channel.primary_avatar ? (
            <div className="flex items-center gap-2">
              {avatarUrl && (
                <img
                  src={avatarUrl}
                  alt={channel.primary_avatar.name || ""}
                  className="w-7 h-7 rounded-full object-cover"
                />
              )}
              <div className="text-right">
                <div className="text-xs text-white/70">{channel.primary_avatar.name}</div>
                <div className="text-[10px] text-white/30">
                  {channel.total_posts} {channel.total_posts === 1 ? "post" : "posts"}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-xs text-white/30">No posts yet</div>
          )}
        </div>

        {/* Action menu */}
        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="p-1 text-white/30 hover:text-white/70 rounded"
          >
            <MoreVertical className="w-4 h-4" />
          </button>
          {menuOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setMenuOpen(false)}
              />
              <div className="absolute right-0 top-7 z-50 min-w-[160px] rounded-md border border-white/10 bg-[#15151f] shadow-xl py-1 text-sm">
                {channel.status === "token_expired" && (
                  <button
                    onClick={() => {
                      setMenuOpen(false);
                      reconnect();
                    }}
                    className="w-full text-left px-3 py-1.5 hover:bg-white/5 text-white/80"
                  >
                    Reconnect
                  </button>
                )}
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    disconnect();
                  }}
                  className="w-full text-left px-3 py-1.5 hover:bg-white/5 text-red-400"
                >
                  Disconnect
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Avatar history (multiple avatars used on this channel) */}
      {channel.avatar_history && channel.avatar_history.length > 1 && (
        <div className="mt-3 pt-3 border-t border-white/5">
          <div className="text-[10px] text-white/30 uppercase tracking-wider mb-1.5">
            Avatars used here
          </div>
          <div className="flex flex-wrap gap-1.5">
            {channel.avatar_history.map((ah) => (
              <span
                key={ah.avatar_id}
                className="flex items-center gap-1 text-[11px] text-white/50 bg-white/5 px-2 py-0.5 rounded-full"
              >
                <Check className="w-3 h-3" /> {ah.avatar_name || "Avatar"} · {ah.post_count}
              </span>
            ))}
          </div>
          {channel.avatar_history.length > 2 && (
            <div className="mt-2 flex items-start gap-1.5 text-[11px] text-amber-300/70">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
              <span>
                Switching avatars on the same channel can confuse followers — try to keep one main face per channel.
              </span>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function ConnectChannelModal({ onClose }: { onClose: () => void }) {
  const startOAuth = async (platformId: string) => {
    try {
      // Without an explicit redirect_uri the backend falls back to a
      // hardcoded production callback URL, which breaks the OAuth
      // round-trip on any other origin (localhost, staging).
      const redirectUri = `${window.location.origin}/integrations/zernio/callback`;
      const res = await socialApi.connectPlatform(platformId, redirectUri);
      const w = 540, h = 720;
      const left = window.screenX + Math.max(0, (window.outerWidth - w) / 2);
      const top = window.screenY + Math.max(0, (window.outerHeight - h) / 2);
      const popup = window.open(
        res.auth_url,
        "zernio_connect",
        `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`,
      );
      if (!popup) {
        toast({
          title: "Popup blocked",
          description: "Allow popups for this site to connect a platform.",
          variant: "destructive",
        });
        return;
      }
      onClose();
    } catch (err: any) {
      toast({
        title: "Could not start connect flow",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-[min(560px,calc(100vw-2rem))] rounded-2xl border border-white/10 bg-[#0f0f1a] p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-1">
          <h2 className="text-base font-semibold text-white">Connect a Channel</h2>
          <button onClick={onClose} className="text-white/40 hover:text-white/80">
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-white/50 mb-4">
          Choose a platform — you'll authorize Luminacast to post on your behalf via Zernio.
        </p>
        <div className="grid grid-cols-2 gap-2">
          {PLATFORMS.map((p) => (
            <button
              key={p.id}
              onClick={() => startOAuth(p.id)}
              className="flex items-center gap-3 p-3 bg-white/[0.04] border border-white/10 rounded-xl hover:bg-white/[0.07] hover:border-white/20 transition text-left"
            >
              <PlatformIcon platform={p.id} className="w-9 h-9" />
              <div className="min-w-0">
                <div className="text-sm font-medium text-white truncate">{p.name}</div>
                <div className="text-[11px] text-white/40 truncate">{p.description}</div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── helpers ─────────────────────────────────────────────────────────────

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
function formatRelativeDate(d: Date): string {
  const days = Math.round((Date.now() - d.getTime()) / 86_400_000);
  if (days < 1) return "today";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  return `${Math.round(days / 365)}y ago`;
}
