import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { channelsApi, userApi } from "@/lib/api";
import type { Channel } from "@/lib/types";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/useToast";
import {
  Plus,
  RefreshCw,
  Pencil,
  Check,
  X,
  Copy,
  Eye,
  EyeOff,
  Trash2,
  Satellite,
  ExternalLink,
  ShoppingBag,
} from "lucide-react";

export function MyChannelsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [showAddModal, setShowAddModal] = useState(false);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["channels"],
    queryFn: () => channelsApi.list(),
  });

  const channels = data?.channels || [];

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">My Channels</h1>
          <p className="text-sm text-text-dim">Connect your TikTok channels to learn your speaking style</p>
        </div>
        <Button onClick={() => setShowAddModal(true)} className="gap-2">
          <Plus className="h-4 w-4" /> Add Channel
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-4">
          {[1, 2].map((i) => (
            <div key={i} className="h-64 animate-pulse rounded-lg bg-card" />
          ))}
        </div>
      )}

      {!isLoading && channels.length === 0 && (
        <div className="rounded-lg border border-border bg-card p-12 text-center">
          <Satellite className="mx-auto mb-4 h-12 w-12 text-text-muted" />
          <h3 className="text-lg font-semibold text-text">No channels connected</h3>
          <p className="mt-1 text-sm text-text-dim">Connect your TikTok channel to get started</p>
          <Button onClick={() => setShowAddModal(true)} className="mt-4">
            Connect Channel
          </Button>
        </div>
      )}

      {channels.map((ch) => (
        <ChannelCard key={ch.id} channel={ch} onRefresh={refetch} />
      ))}

      {/* Affiliate accounts -- referenced by /settings/channels#affiliates */}
      <div id="affiliates" className="scroll-mt-4">
        <AffiliateAccounts />
      </div>

      {showAddModal && (
        <AddChannelModal
          onClose={() => setShowAddModal(false)}
          onCreated={() => {
            setShowAddModal(false);
            refetch();
          }}
        />
      )}
    </div>
  );
}

// ── Affiliate accounts ──
//
// Two known platforms today (TikTok Shop, Amazon Associates). The "+ Add
// affiliate account" button is intentionally future-only -- spec defers
// the dynamic-add UI; see TODO future: extensible affiliate platform list.

function AffiliateAccounts() {
  const { data, isLoading } = useQuery({
    queryKey: ["my-affiliates"],
    queryFn: () => userApi.getAffiliates(),
  });

  return (
    <div className="rounded-lg border border-border bg-card p-6 space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-text">Affiliate Accounts</h3>
        <p className="text-sm text-text-dim">
          Connect your affiliate IDs so we can attach them automatically to
          imported product links.
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          <div className="h-12 animate-pulse rounded-md bg-surface" />
          <div className="h-12 animate-pulse rounded-md bg-surface" />
        </div>
      ) : (
        <div className="space-y-3">
          <AffiliateRow
            platform="tiktok"
            label="TikTok Shop"
            value={data?.tiktok_affiliate_id ?? null}
            placeholder="your-affiliate-id"
            icon={
              // Inline SVG matches the SourceBadge glyph used on product cards.
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden>
                <path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.12v-3.49a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.16 15.8a6.34 6.34 0 0010.86 4.47V13.4a8.28 8.28 0 005.57 2.14v-3.44a4.85 4.85 0 01-3.57-1.98V6.69h3.57z" />
              </svg>
            }
          />
          <AffiliateRow
            platform="amazon"
            label="Amazon Associates"
            value={data?.amazon_associate_tag ?? null}
            placeholder="your-tag-20"
            icon={<ShoppingBag className="h-4 w-4" />}
            helpUrl="https://affiliate-program.amazon.com"
            helpText="Sign up free at affiliate-program.amazon.com"
          />
        </div>
      )}

      {/* TODO future: extensible affiliate platform list -- swap the two
          fixed rows above for a dynamic list driven by a server-supplied
          `availableAffiliatePlatforms` registry, plus a real "+ Add
          affiliate account" modal. For now the spec calls out only
          TikTok Shop + Amazon Associates, so we render those statically. */}
    </div>
  );
}

function AffiliateRow({
  platform,
  label,
  value,
  placeholder,
  icon,
  helpUrl,
  helpText,
}: {
  platform: "tiktok" | "amazon";
  label: string;
  value: string | null;
  placeholder: string;
  icon: React.ReactNode;
  helpUrl?: string;
  helpText?: string;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");

  // Keep local draft in sync when the server value changes (initial
  // fetch, or after a sibling row's mutation invalidates the query).
  useEffect(() => {
    if (!editing) setDraft(value ?? "");
  }, [value, editing]);

  const updateMutation = useMutation({
    mutationFn: (next: string | null) => userApi.updateAffiliate(platform, next),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-affiliates"] });
      // The product detail page reads `amazon_associate_tag` from the
      // auth store to decide whether to show the amber "connect Amazon"
      // callout, so refresh the persisted user record too.
      void fetchUser();
      setEditing(false);
      toast({ title: `${label} updated` });
    },
    onError: (err: any) => {
      toast({
        title: `Could not update ${label}`,
        description: err?.response?.data?.detail || err?.message || "Try again",
        variant: "destructive",
      });
    },
  });

  const connected = !!value && value.trim().length > 0;

  const save = () => {
    const trimmed = draft.trim();
    updateMutation.mutate(trimmed.length === 0 ? null : trimmed);
  };

  const clear = () => updateMutation.mutate(null);

  return (
    <div className="rounded-md border border-border bg-surface p-3">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-card text-text">
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-text">{label}</span>
            {connected ? (
              <span className="rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-medium text-success">
                Connected
              </span>
            ) : (
              <span className="rounded-full bg-text-muted/15 px-2 py-0.5 text-[10px] font-medium text-text-muted">
                Not connected
              </span>
            )}
          </div>
          {!editing && connected && (
            <div className="mt-0.5 truncate font-mono text-xs text-text-dim">{value}</div>
          )}
          {!editing && !connected && helpText && (
            <div className="mt-0.5 text-xs text-text-muted">
              {helpUrl ? (
                <a
                  href={helpUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-accent hover:underline"
                >
                  {helpText} <ExternalLink className="h-3 w-3" />
                </a>
              ) : (
                helpText
              )}
            </div>
          )}
        </div>

        {!editing ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => { setDraft(value ?? ""); setEditing(true); }}
            aria-label={connected ? `Edit ${label}` : `Connect ${label}`}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
        ) : null}
      </div>

      {editing && (
        <div className="mt-3 flex items-center gap-2">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            maxLength={100}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") { setEditing(false); setDraft(value ?? ""); }
            }}
          />
          <Button size="sm" onClick={save} disabled={updateMutation.isPending}>
            <Check className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => { setEditing(false); setDraft(value ?? ""); }}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
          {connected && (
            <Button
              size="sm"
              variant="ghost"
              className="text-danger"
              onClick={clear}
              disabled={updateMutation.isPending}
              title="Disconnect"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function ChannelCard({ channel: ch, onRefresh }: { channel: Channel; onRefresh: () => void }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [showStreamKey, setShowStreamKey] = useState(false);
  const [showAllPhrases, setShowAllPhrases] = useState(false);

  // Poll while indexing
  const isIndexing = ch.index_status === "indexing";
  useEffect(() => {
    if (!isIndexing) return;
    const interval = setInterval(() => onRefresh(), 5000);
    return () => clearInterval(interval);
  }, [isIndexing, onRefresh]);

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, string>) => channelsApi.update(ch.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["channels"] });
      setEditing(null);
      toast({ title: "Updated" });
    },
  });

  const reindexMutation = useMutation({
    mutationFn: () => channelsApi.reindex(ch.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["channels"] });
      toast({ title: "Re-indexing started" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => channelsApi.delete(ch.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["channels"] });
      toast({ title: "Channel disconnected" });
    },
  });

  const startEdit = (field: string, value: string) => {
    setEditing(field);
    setEditValue(value);
  };

  const saveEdit = (field: string) => {
    updateMutation.mutate({ [field]: editValue });
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast({ title: "Copied to clipboard" });
  };

  const vp = ch.voice_profile;
  const indexProgress = ch.index_target_count > 0
    ? Math.round((ch.indexed_video_count / ch.index_target_count) * 100)
    : 0;

  const EditableField = ({ label, field, value }: { label: string; field: string; value: string }) => (
    <div className="flex items-center gap-2">
      <span className="text-sm text-text-dim w-28">{label}:</span>
      {editing === field ? (
        <>
          <Input
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            className="h-8 flex-1"
            autoFocus
          />
          <Button size="sm" variant="ghost" onClick={() => saveEdit(field)}>
            <Check className="h-3 w-3" />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
            <X className="h-3 w-3" />
          </Button>
        </>
      ) : (
        <>
          <span className="flex-1 text-sm text-text">{value || "—"}</span>
          <Button size="sm" variant="ghost" onClick={() => startEdit(field, value)}>
            <Pencil className="h-3 w-3" />
          </Button>
        </>
      )}
    </div>
  );

  return (
    <div className="rounded-lg border border-border bg-card p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-lg font-semibold text-text capitalize">{ch.platform}</span>
          <span className="text-text-dim">·</span>
          <span className="text-text">{ch.handle}</span>
        </div>
        <span className="rounded-full bg-green-500/10 px-3 py-1 text-xs font-medium text-green-500">
          Connected
        </span>
      </div>

      {/* Editable fields */}
      <div className="space-y-2">
        <EditableField label="Display name" field="display_name" value={ch.display_name} />
        <EditableField label="Bio" field="bio" value={ch.bio} />
        <EditableField label="Handle" field="handle" value={ch.handle} />
      </div>

      {/* Stats */}
      <div className="flex gap-4 text-sm text-text-dim">
        <span>{ch.followers_count.toLocaleString()} followers</span>
        <span>·</span>
        <span>{ch.video_count.toLocaleString()} videos</span>
      </div>

      {/* Content indexing */}
      <div className="border-t border-border pt-4">
        <h4 className="text-sm font-semibold text-text mb-2">Content indexing</h4>
        {isIndexing ? (
          <div>
            <div className="flex justify-between text-xs text-text-dim mb-1">
              <span>Indexed: {ch.indexed_video_count} of {ch.index_target_count} videos</span>
              <span>{indexProgress}%</span>
            </div>
            <div className="h-2 rounded-full bg-surface">
              <div
                className="h-2 rounded-full bg-accent transition-all"
                style={{ width: `${indexProgress}%` }}
              />
            </div>
          </div>
        ) : ch.index_status === "completed" ? (
          <div className="text-sm text-text-dim">
            <p>Indexed: {ch.indexed_video_count} videos ({ch.relevant_video_count} relevant, {(ch.indexed_video_count - ch.relevant_video_count)} filtered out)</p>
          </div>
        ) : ch.index_status === "failed" ? (
          <p className="text-sm text-danger">Indexing failed. Try re-indexing.</p>
        ) : (
          <p className="text-sm text-text-dim">Pending</p>
        )}
      </div>

      {/* Voice profile */}
      {vp && vp.tone && vp.tone !== "unknown" && (
        <div className="border-t border-border pt-4">
          <h4 className="text-sm font-semibold text-text mb-2">Voice profile</h4>
          <p className="text-sm text-text-dim mb-2">Tone: {vp.tone}</p>

          {vp.common_phrases && vp.common_phrases.length > 0 && (
            <div className="mb-2">
              <p className="text-xs text-text-muted mb-1">
                Common phrases ({vp.common_phrases.length} detected):
              </p>
              <div className="flex flex-wrap gap-1">
                {(showAllPhrases ? vp.common_phrases : vp.common_phrases.slice(0, 10)).map((p, i) => (
                  <span key={i} className="rounded bg-surface px-2 py-0.5 text-xs text-text">
                    "{p}"
                  </span>
                ))}
              </div>
              {vp.common_phrases.length > 10 && (
                <button
                  onClick={() => setShowAllPhrases(!showAllPhrases)}
                  className="mt-1 text-xs text-accent hover:underline"
                >
                  {showAllPhrases ? "Show less" : `Show all ${vp.common_phrases.length}`}
                </button>
              )}
            </div>
          )}

          {vp.topics && vp.topics.length > 0 && (
            <p className="text-sm text-text-dim">
              Topics: {vp.topics.join(", ")}
            </p>
          )}
        </div>
      )}

      {/* Streaming credentials */}
      <div className="border-t border-border pt-4">
        <h4 className="text-sm font-semibold text-text mb-2">Streaming credentials</h4>
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm text-text-dim w-24">Stream URL:</span>
            {editing === "stream_url" ? (
              <>
                <Input value={editValue} onChange={(e) => setEditValue(e.target.value)} className="h-8 flex-1" />
                <Button size="sm" variant="ghost" onClick={() => saveEdit("stream_url")}><Check className="h-3 w-3" /></Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(null)}><X className="h-3 w-3" /></Button>
              </>
            ) : (
              <>
                <span className="flex-1 text-sm text-text font-mono truncate">{ch.stream_url || "—"}</span>
                <Button size="sm" variant="ghost" onClick={() => startEdit("stream_url", ch.stream_url)}><Pencil className="h-3 w-3" /></Button>
                {ch.stream_url && <Button size="sm" variant="ghost" onClick={() => copyToClipboard(ch.stream_url)}><Copy className="h-3 w-3" /></Button>}
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-text-dim w-24">Stream key:</span>
            {editing === "stream_key" ? (
              <>
                <Input value={editValue} onChange={(e) => setEditValue(e.target.value)} className="h-8 flex-1" />
                <Button size="sm" variant="ghost" onClick={() => saveEdit("stream_key")}><Check className="h-3 w-3" /></Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(null)}><X className="h-3 w-3" /></Button>
              </>
            ) : (
              <>
                <span className="flex-1 text-sm text-text font-mono truncate">
                  {showStreamKey ? ch.stream_key || "—" : "••••••••••••••••••"}
                </span>
                <Button size="sm" variant="ghost" onClick={() => setShowStreamKey(!showStreamKey)}>
                  {showStreamKey ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => startEdit("stream_key", ch.stream_key)}><Pencil className="h-3 w-3" /></Button>
                {ch.stream_key && <Button size="sm" variant="ghost" onClick={() => copyToClipboard(ch.stream_key)}><Copy className="h-3 w-3" /></Button>}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-2 border-t border-border pt-4">
        <Button variant="outline" size="sm" onClick={() => reindexMutation.mutate()} disabled={isIndexing}>
          <RefreshCw className={`h-3 w-3 mr-1 ${isIndexing ? "animate-spin" : ""}`} />
          Re-index content
        </Button>
        <Button variant="outline" size="sm" className="text-danger" onClick={() => {
          if (confirm("Disconnect this channel?")) deleteMutation.mutate();
        }}>
          <Trash2 className="h-3 w-3 mr-1" />
          Disconnect
        </Button>
      </div>
    </div>
  );
}

function AddChannelModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { toast } = useToast();
  const [platform, setPlatform] = useState("tiktok");
  const [handle, setHandle] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [streamKey, setStreamKey] = useState("");
  const [streamUrl, setStreamUrl] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      channelsApi.create({
        platform,
        handle,
        display_name: displayName,
        stream_key: streamKey,
        stream_url: streamUrl,
      }),
    onSuccess: () => {
      toast({ title: "Channel connected! Analyzing your content..." });
      onCreated();
    },
    onError: (e: any) => {
      toast({ title: "Error", description: e.response?.data?.detail || "Failed to connect channel", variant: "destructive" });
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg bg-card p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-text mb-4">Connect Channel</h2>

        {/* Platform picker */}
        <div className="mb-4">
          <label className="text-sm text-text-dim mb-1 block">Platform</label>
          <div className="flex gap-2">
            <Button
              variant={platform === "tiktok" ? "default" : "outline"}
              size="sm"
              onClick={() => setPlatform("tiktok")}
            >
              TikTok
            </Button>
            <Button variant="outline" size="sm" disabled className="opacity-50">
              Instagram
              <span className="ml-1 text-[10px]">Soon</span>
            </Button>
            <Button variant="outline" size="sm" disabled className="opacity-50">
              YouTube
              <span className="ml-1 text-[10px]">Soon</span>
            </Button>
          </div>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-sm text-text-dim mb-1 block">Handle</label>
            <Input
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="@yourusername"
            />
          </div>
          <div>
            <label className="text-sm text-text-dim mb-1 block">Display name (optional)</label>
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Your Name"
            />
          </div>
          <div>
            <label className="text-sm text-text-dim mb-1 block">Stream key (optional)</label>
            <Input
              value={streamKey}
              onChange={(e) => setStreamKey(e.target.value)}
              placeholder="Can add later"
            />
          </div>
          <div>
            <label className="text-sm text-text-dim mb-1 block">Stream URL (optional)</label>
            <Input
              value={streamUrl}
              onChange={(e) => setStreamUrl(e.target.value)}
              placeholder="rtmp://..."
            />
          </div>
        </div>

        <p className="mt-3 text-xs text-text-muted">
          We'll analyze your content to learn your speaking style. This takes ~15 minutes.
        </p>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={() => createMutation.mutate()}
            disabled={!handle.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? "Connecting..." : "Connect"}
          </Button>
        </div>
      </div>
    </div>
  );
}
