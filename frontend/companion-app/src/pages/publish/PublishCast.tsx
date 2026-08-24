import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Sparkles, Calendar, Loader2, Check, AlertTriangle, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { castsApi, socialApi, confirmConnectWithRetry } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import { oneLineSummary } from "@/lib/oneLineSummary";

type Platform = "tiktok" | "instagram" | "youtube" | "linkedin" | "facebook";

const PLATFORMS: { value: Platform; label: string }[] = [
  { value: "tiktok", label: "TikTok" },
  { value: "instagram", label: "Instagram" },
  { value: "youtube", label: "YouTube" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "facebook", label: "Facebook" },
];

/**
 * Publish page \u2014 schedule or post a rendered cast across platforms.
 *
 * Reachable from /publish/:castId. Queries the cast's render to get the
 * preview video URL, lets the user pick platforms + caption + schedule,
 * then calls POST /social/posts which talks to Zernio.
 */
// The Schedule tab's inline PublishCard (PublishHub.tsx) uses its own
// looser platform keys (e.g. "instagram_reels") when handing off here via
// query params — collapse to this page's base Platform keys.
function normalizePlatform(raw: string): Platform | null {
  const base = raw.split("_")[0] as Platform;
  return PLATFORMS.some((p) => p.value === base) ? base : null;
}

export default function PublishCast() {
  const { castId } = useParams<{ castId: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const cid = castId || "";

  const { data: cast } = useQuery({
    queryKey: ["cast", cid],
    queryFn: () => castsApi.get(cid),
    enabled: !!cid,
  });

  // Editing an existing scheduled/failed post — carried via ?post_id= from
  // the Publishing tab's Edit button. Zernio has no update endpoint (only
  // create/get/delete), so "editing" means: load the original post's data
  // to prefill this form, then on submit delete the old post and create a
  // replacement with the edited fields.
  const postId = searchParams.get("post_id");
  const { data: existingPost } = useQuery({
    queryKey: ["social-post", postId],
    queryFn: () => socialApi.getPost(postId!),
    enabled: !!postId,
  });

  const queryClient = useQueryClient();
  const { data: profiles, isError: profilesError, error: profilesErr } = useQuery({
    queryKey: ["social-profiles"],
    queryFn: () => socialApi.listProfiles(),
    retry: false,
  });

  // Listen for the OAuth-callback popup posting a message back. The popup
  // page (/integrations/zernio/callback) calls window.opener.postMessage(...)
  // when Zernio finishes the OAuth dance. We refresh the profiles list so
  // the newly-connected platform tile flips from “not connected” → active.
  useEffect(() => {
    async function onMessage(ev: MessageEvent) {
      if (typeof ev.data !== "object" || ev.data === null) return;
      if ((ev.data as any).type !== "zernio-connected") return;
      // "zernio-connected" is just the message channel's name — it fires
      // for a failed/declined OAuth too, with the real outcome carried in
      // status/error. Ignoring those meant every attempt (including the
      // user closing the popup without authorizing) showed a false
      // "Account connected" success toast.
      const { platform, status, error, accountId } = ev.data as any;
      if (error || status !== "ok") {
        // Zernio's real explanation (from the connect-error lookup) can
        // read like a support article — fine on the callback popup, which
        // has room, but needs collapsing to fit a toast.
        const description = error
          ? oneLineSummary(error)
          : `Failed to connect ${platform || "platform"}.`;
        toast({
          title: "Could not connect",
          description,
          variant: "destructive",
        });
        return;
      }
      // Attribute the newly-connected account to this user BEFORE
      // refreshing /profiles — that endpoint now only returns accounts
      // this user actually owns (fixing a leak where it used to return
      // every Luminacast customer's connected accounts), so without this
      // call the just-connected account would never appear here. Retries
      // internally since a slow Zernio call can make the first attempt
      // look unclaimed when the account just isn't visible yet.
      const res = await confirmConnectWithRetry(platform, accountId);
      const claimed = res.claimed;
      queryClient.invalidateQueries({ queryKey: ["social-profiles"] });
      if (claimed) {
        toast({
          title: "Account connected",
          description: `Connected ${platform || "platform"}.`,
          variant: "success",
        });
      } else if (res.reason === "owned_by_other_user") {
        toast({
          title: "Already connected to a different account",
          description: `This ${platform || "platform"} account is linked to another Luminacast login. Sign in with that account, or disconnect it there first.`,
          variant: "destructive",
        });
      } else {
        toast({
          title: "Connected, but couldn't confirm the account",
          description: "Refresh this page — if it's still missing, try connecting again.",
          variant: "destructive",
        });
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [queryClient]);

  // Which platform's connect flow is in flight — connectPlatform() now also
  // resolves the Zernio workspace profile server-side before returning the
  // auth URL, so the request can take a moment; the button needs a visible
  // pending state or a slow click looks like nothing happened.
  const [connectingPlatform, setConnectingPlatform] = useState<string | null>(null);

  const handleConnect = useCallback(async (platform: string) => {
    setConnectingPlatform(platform);
    try {
      // Without an explicit redirect_uri, the backend falls back to its
      // hardcoded production callback URL — so testing this flow anywhere
      // other than production (localhost, staging) sends Zernio's OAuth
      // redirect to a domain the current tab never opened, and the
      // "zernio-connected" postMessage never reaches this window's opener
      // relationship. Always point back at whatever origin is actually
      // running this page.
      const redirectUri = `${window.location.origin}/integrations/zernio/callback`;
      const res = await socialApi.connectPlatform(platform, redirectUri);
      // Open Zernio's OAuth URL in a centered popup.
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
      }
    } catch (err: any) {
      toast({
        title: "Could not start connect flow",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setConnectingPlatform(null);
    }
  }, []);
  const zernioMissing =
    profilesError && (profilesErr as any)?.response?.status === 503;

  // Arriving from the Schedule tab's inline PublishCard (PublishHub.tsx)
  // carries the user's selections as query params — platforms, mode
  // (now/later), and the picked datetime — specifically so they don't have
  // to redo them here. None of these were being read; this page always
  // silently reset to its own defaults (Post now, no platforms preselected).
  const [selectedPlatforms, setSelectedPlatforms] = useState<Platform[]>(() => {
    const raw = searchParams.get("platforms");
    if (!raw) return ["tiktok"];
    const mapped = raw.split(",").map(normalizePlatform).filter((p): p is Platform => !!p);
    return mapped.length ? mapped : ["tiktok"];
  });
  const [caption, setCaption] = useState(() => searchParams.get("caption") || "");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [hashtagInput, setHashtagInput] = useState("");
  const [firstComment, setFirstComment] = useState("");

  const [generatingCaption, setGeneratingCaption] = useState(false);

  const [postNow, setPostNow] = useState(() => searchParams.get("mode") !== "later");
  // datetime-local value — the source (PublishHub's PublishCard) uses the
  // same input type, so the raw query value is already in the right shape.
  const [scheduledAt, setScheduledAt] = useState<string>(() => searchParams.get("at") || "");
  const [submitting, setSubmitting] = useState(false);

  // Auto-generate the caption on first load, once we have the cast. Skipped
  // entirely when editing an existing post — its own caption is loaded via
  // the hydration effect below instead, and an empty `caption` state while
  // that fetch is still in flight must not race into generating a fresh one.
  useEffect(() => {
    if (!cid || !cast || caption || generatingCaption || postId) return;
    setGeneratingCaption(true);
    socialApi
      .generateCaption({ cast_id: cid, platform: selectedPlatforms[0] || "tiktok" })
      .then((res) => {
        setCaption(res.caption);
        setHashtags(res.hashtags || []);
        setFirstComment(res.first_comment || "");
      })
      .catch(() => { })
      .finally(() => setGeneratingCaption(false));
  }, [cid, cast]); // eslint-disable-line react-hooks/exhaustive-deps

  // Prefill the form from the post being edited. `caption` already has
  // hashtags baked into its text at creation time (see backend
  // create_social_post's full_caption), so the hashtag chips start empty
  // here rather than duplicating them — the caption box shows exactly what
  // was actually posted/scheduled. first_comment isn't persisted anywhere
  // on SocialPost, so it can't be recovered here.
  const hydratedFromPostRef = useRef(false);
  useEffect(() => {
    if (!existingPost || hydratedFromPostRef.current) return;
    hydratedFromPostRef.current = true;
    setCaption(existingPost.caption || "");
    const platforms = (existingPost.platforms || [])
      .map((p: any) => normalizePlatform(p.platform || ""))
      .filter((p: Platform | null): p is Platform => !!p);
    if (platforms.length) setSelectedPlatforms(platforms);
    if (existingPost.scheduled_for) {
      setPostNow(false);
      // datetime-local input needs "YYYY-MM-DDTHH:mm" in local time.
      const d = new Date(existingPost.scheduled_for);
      const pad = (n: number) => String(n).padStart(2, "0");
      setScheduledAt(
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`,
      );
    } else {
      setPostNow(true);
    }
  }, [existingPost]);

  // A platform can now have more than one connected account (e.g. two
  // TikTok accounts) — group all of them per platform rather than
  // collapsing to just the first.
  const accountsByPlatform = useMemo(() => {
    const map: Partial<Record<Platform, typeof profiles>> = {};
    (profiles || []).forEach((p) => {
      const platform = p.platform as Platform;
      if (!platform) return;
      (map[platform] ??= []).push(p);
    });
    return map;
  }, [profiles]);

  // Which specific account to publish to, per platform — only meaningful
  // (and shown) when a platform has more than one connected account.
  // Defaults to the first account until the user picks one explicitly.
  const [selectedAccountByPlatform, setSelectedAccountByPlatform] = useState<Record<string, string>>({});

  const accountByPlatform = useMemo(() => {
    const map: Record<string, string | undefined> = {};
    (Object.keys(accountsByPlatform) as Platform[]).forEach((platform) => {
      const accounts = accountsByPlatform[platform] || [];
      const chosen = selectedAccountByPlatform[platform];
      map[platform] = (chosen && accounts.some((a) => a._id === chosen))
        ? chosen
        : accounts[0]?._id;
    });
    return map;
  }, [accountsByPlatform, selectedAccountByPlatform]);

  // A platform preselected via the ?platforms= query param (from the
  // Schedule tab handoff) can be one the user never actually connected —
  // sending that to Zernio puts a null accountId in the platforms array,
  // which 400s the ENTIRE post, not just that one platform ("Invalid
  // input: expected string, received null", param platforms.N.accountId).
  // Once we know which accounts are really connected, drop anything
  // preselected that isn't.
  useEffect(() => {
    if (!profiles) return; // still loading — don't clear based on no data yet
    setSelectedPlatforms((sel) => {
      const filtered = sel.filter((p) => !!accountByPlatform[p]);
      return filtered.length === sel.length ? sel : filtered;
    });
  }, [profiles, accountByPlatform]);

  const togglePlatform = (p: Platform) =>
    setSelectedPlatforms((sel) =>
      sel.includes(p) ? sel.filter((x) => x !== p) : [...sel, p],
    );

  const handleAddHashtag = () => {
    const v = hashtagInput.trim().replace(/^#/, "");
    if (!v) return;
    if (hashtags.includes(v)) return;
    setHashtags([...hashtags, v]);
    setHashtagInput("");
  };

  const handleRegenerateCaption = async () => {
    if (!cid) return;
    setGeneratingCaption(true);
    try {
      const res = await socialApi.generateCaption({
        cast_id: cid,
        platform: selectedPlatforms[0] || "tiktok",
      });
      setCaption(res.caption);
      setHashtags(res.hashtags || []);
      setFirstComment(res.first_comment || "");
    } catch (err: any) {
      toast({
        title: "Caption generation failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setGeneratingCaption(false);
    }
  };

  // `datetime-local` values have no timezone, so treat them as local time
  // to match what `new Date(scheduledAt)` does at submit — a naive string
  // comparison against an ISO `now` would drift by the local UTC offset.
  const minScheduleValue = useMemo(() => {
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    return now.toISOString().slice(0, 16);
  }, []);
  const isScheduledInPast = !postNow && !!scheduledAt && new Date(scheduledAt).getTime() <= Date.now();

  const canSubmit = !!cast && selectedPlatforms.length > 0 && caption.trim() && !submitting && !isScheduledInPast;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      // Zernio has no update endpoint — "editing" replaces the old post
      // with a new one carrying the edited fields. Delete first so a
      // create failure doesn't leave the user with two posts.
      if (postId) {
        await socialApi.deletePost(postId);
      }
      const scheduledIso = postNow || !scheduledAt ? null : new Date(scheduledAt).toISOString();
      await socialApi.createPost({
        cast_id: cid,
        caption: caption.trim(),
        hashtags,
        first_comment: firstComment.trim() || null,
        platforms: selectedPlatforms.map((p) => ({
          platform: p,
          accountId: accountByPlatform[p],
        })),
        scheduled_for: scheduledIso,
        publish_now: postNow,
      });
      toast({
        title: postId ? "Post updated" : postNow ? "Posting now" : "Scheduled",
        description: postNow
          ? "Your post is being published."
          : `Will post at ${new Date(scheduledAt).toLocaleString()}.`,
      });
      // /published is the old standalone page — /publish now has its own
      // Published tab (with the opportunistic Zernio status refresh), so
      // route there instead of the redundant page.
      navigate(postNow ? "/publish?tab=published" : "/publish?tab=scheduled");
    
    } catch (err: any) {
      toast({
        title: "Could not publish",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-5">
      <div className="flex items-center gap-2">
        <Send className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-white">{postId ? "Edit Post" : "Publish"}</h1>
        {cast?.name && (
          <span className="text-sm text-white/50">— {cast.name}</span>
        )}
      </div>

      {zernioMissing && (
        <div className="flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 p-3 text-xs text-yellow-200">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <p>
            Connect your social-media account to start publishing. Set
            <code className="mx-1 px-1 rounded bg-black/40 font-mono">ZERNIO_API_KEY</code>
            in your environment, or contact support.
          </p>
        </div>
      )}

      <div className="flex items-center justify-between rounded-md border border-white/10 bg-white/[0.02] px-3 py-2 text-[11px]">
        <span className="text-white/50">
          Manage which accounts you can publish to in My Channels.
        </span>
        <button
          onClick={() => navigate("/channels")}
          className="text-accent hover:text-accent/80 font-medium"
        >
          Open My Channels →
        </button>
      </div>

      <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-4">
        <h2 className="text-sm font-semibold text-white/80">Where</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {PLATFORMS.map((p) => {
            const active = selectedPlatforms.includes(p.value);
            const connected = !!accountByPlatform[p.value];
            if (!connected) {
              const connecting = connectingPlatform === p.value;
              return (
                <button
                  key={p.value}
                  onClick={() => handleConnect(p.value)}
                  disabled={connecting}
                  className="relative rounded-md border border-dashed border-white/15 px-3 py-2 text-sm text-white/60 hover:border-accent/60 hover:text-white transition cursor-pointer disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:border-white/15 disabled:hover:text-white/60"
                  title={`Connect ${p.label} via Zernio`}
                >
                  <span className="flex items-center gap-1.5">
                    {connecting ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Link2 className="h-3.5 w-3.5" />
                    )}
                    {connecting ? "Connecting…" : `Connect ${p.label}`}
                  </span>
                </button>
              );
            }
            return (
              <button
                key={p.value}
                onClick={() => togglePlatform(p.value)}
                className={`relative rounded-md border px-3 py-2 text-sm transition cursor-pointer ${active
                    ? "border-accent bg-accent/10 text-white"
                    : "border-white/10 text-white/70 hover:border-white/20"
                  }`}
              >
                {p.label}
              </button>
            );
          })}
        </div>

        {/* A platform can have more than one connected account (e.g. two
            TikTok accounts) — once selected, let the user pick which one
            to actually publish to instead of silently always using the
            first one connected. */}
        {selectedPlatforms
          .filter((p) => (accountsByPlatform[p]?.length || 0) > 1)
          .map((p) => {
            const accounts = accountsByPlatform[p] || [];
            const label = PLATFORMS.find((pl) => pl.value === p)?.label || p;
            return (
              <div key={p} className="flex items-center gap-2 text-sm">
                <span className="text-white/50 w-20 shrink-0">{label} as</span>
                <select
                  value={accountByPlatform[p] || ""}
                  onChange={(e) =>
                    setSelectedAccountByPlatform((prev) => ({ ...prev, [p]: e.target.value }))
                  }
                  className="flex-1 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1.5 text-white/85 focus:outline-none focus:border-accent/40"
                >
                  {accounts.map((a) => (
                    <option key={a._id} value={a._id}>
                      {a.username ? `@${a.username}` : a.displayName || a._id}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
      </section>

      <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white/80">Caption</h2>
          <Button
            size="sm" variant="outline"
            onClick={handleRegenerateCaption}
            disabled={generatingCaption}
          >
            {generatingCaption ? (
              <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
            ) : (
              <Sparkles className="h-3.5 w-3.5 mr-1" />
            )}
            Regenerate
          </Button>
        </div>
        <textarea
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          rows={4}
          placeholder="Write your caption…"
          className="w-full rounded-md bg-white/5 border border-white/10 px-3 py-2 text-sm text-white placeholder-white/30"
        />

        <div className="space-y-1">
          <label className="text-xs text-white/50">Hashtags</label>
          <div className="flex flex-wrap gap-1.5">
            {hashtags.map((h) => (
              <button
                key={h}
                onClick={() => setHashtags(hashtags.filter((x) => x !== h))}
                className="rounded-full bg-accent/15 text-accent px-2 py-0.5 text-xs hover:bg-red-500/20 hover:text-red-300"
              >
                #{h} ×
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              value={hashtagInput}
              onChange={(e) => setHashtagInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); handleAddHashtag(); }
              }}
              placeholder="add a hashtag"
              className="flex-1 rounded-md bg-white/5 border border-white/10 px-2 py-1 text-xs text-white placeholder-white/30"
            />
            <Button size="sm" variant="outline" onClick={handleAddHashtag}>Add</Button>
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-white/50">
            First comment (Instagram / LinkedIn) — optional
          </label>
          <textarea
            value={firstComment}
            onChange={(e) => setFirstComment(e.target.value)}
            rows={2}
            placeholder="More hashtags or a CTA — auto-posted as the first comment when supported."
            className="w-full rounded-md bg-white/5 border border-white/10 px-3 py-2 text-xs text-white placeholder-white/30"
          />
        </div>
      </section>

      <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5 space-y-3">
        <h2 className="text-sm font-semibold text-white/80">When</h2>
        <div className="flex gap-3">
          <button
            onClick={() => setPostNow(true)}
            className={`flex-1 rounded-md border px-3 py-2 text-sm ${postNow
                ? "border-accent bg-accent/10 text-white"
                : "border-white/10 text-white/70 hover:border-white/20"
              }`}
          >
            Post now
          </button>
          <button
            onClick={() => setPostNow(false)}
            className={`flex-1 rounded-md border px-3 py-2 text-sm ${!postNow
                ? "border-accent bg-accent/10 text-white"
                : "border-white/10 text-white/70 hover:border-white/20"
              }`}
          >
            Schedule
          </button>
        </div>
        {!postNow && (
          <div className="space-y-1">
            <label className="text-xs text-white/50 flex items-center gap-1">
              <Calendar className="h-3.5 w-3.5" /> Date & time
            </label>
            <input
              type="datetime-local"
              value={scheduledAt}
              min={minScheduleValue}
              onChange={(e) => setScheduledAt(e.target.value)}
              className="rounded-md bg-white/5 border border-white/10 px-3 py-2 text-sm text-white"
            />
            {isScheduledInPast && (
              <p className="text-xs text-red-400">
                You cannot schedule a post for a past date and time. Please select a future date and time.
              </p>
            )}
          </div>
        )}
      </section>

      <Button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="w-full bg-accent hover:bg-accent/90 py-3"
      >
        {submitting ? (
          <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Working…</>
        ) : postId ? (
          <><Check className="h-4 w-4 mr-2" /> Save changes</>
        ) : postNow ? (
          <><Send className="h-4 w-4 mr-2" /> Post now to {selectedPlatforms.length} platform{selectedPlatforms.length !== 1 ? "s" : ""}</>
        ) : (
          <><Calendar className="h-4 w-4 mr-2" /> Schedule</>
        )}
      </Button>
    </div>
  );
}
