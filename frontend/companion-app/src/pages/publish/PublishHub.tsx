import { useState, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueries } from "@tanstack/react-query";
import {
  Calendar,
  Send,
  MessageCircle,
  Plus,
  ExternalLink,
  Trash2,
  Sparkles,
  Loader2,
  ChevronLeft,
  ChevronRight,
  Pencil,
  X,
  Check,
  AlertTriangle,
  ShieldAlert,
  Film,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { socialApi, castsApi } from "@/lib/api";
import { cn } from "@/lib/cn";
import { toast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";
import { PlatformIcon, platformLabel } from "@/components/common/PlatformIcon";

/**
 * Publish — daily workspace for scheduling, monitoring published
 * posts, and managing comments. Three tabs (Schedule / Published /
 * Comments) plus a calendar header on the Schedule tab.
 *
 * Channel connection / disconnection lives on a separate page under
 * Settings → My Channels (intentionally split: Publish is daily
 * work, Channels is one-time setup).
 *
 * URL: /publish  with optional ?tab=schedule|published|comments
 */

type Tab = "schedule" | "scheduled" | "publishing" | "published" | "comments";

const VIDEO_EXTENSIONS = [".mp4", ".mov", ".webm", ".m4v"];

/** post.media_url is the render's actual video file, not a still image —
 * an <img> tag can't decode video and silently shows a broken-image icon.
 * Detect video URLs and render a muted <video> instead, which browsers
 * paint with the first frame as a static preview even without playback. */
function MediaThumb({ url, className }: { url: string; className?: string }) {
  const isVideo = VIDEO_EXTENSIONS.some((ext) =>
    url.split("?", 1)[0].toLowerCase().endsWith(ext),
  );
  if (isVideo) {
    return (
      <video
        src={url}
        className={className}
        muted
        playsInline
        preload="metadata"
      />
    );
  }
  return <img src={url} alt="" className={className} />;
}

export default function PublishHub() {
  const [params, setParams] = useSearchParams();
  const initialTab = (params.get("tab") as Tab) || "schedule";
  const [tab, setTab] = useState<Tab>(initialTab);

  const setTabAndUrl = (t: Tab) => {
    setTab(t);
    const next = new URLSearchParams(params);
    next.set("tab", t);
    setParams(next, { replace: true });
  };

  // Pending-comment count powers the badge on the Comments tab. Previously
  // approximated as "the user has any posts at all", which is true for
  // nearly every active user regardless of actual unread comments — the
  // dot never turned off. Now backed by a real count of comments with
  // reply_status === "pending" across all of the user's posts.
  const { data: pendingCommentData } = useQuery({
    queryKey: ["pending-comment-count"],
    queryFn: () => socialApi.getPendingCommentCount(),
    refetchInterval: 60_000,
  });
  const pendingHint = (pendingCommentData?.count || 0) > 0;

  return (
    <div className="max-w-5xl mx-auto px-6 py-6 space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Publish</h1>
          <p className="text-xs text-white/40 mt-0.5">
            Publish your videos, track performance, and manage comments.
          </p>
        </div>
      </header>

      <div className="flex gap-1 bg-white/5 p-1 rounded-xl w-fit">
        <TabButton
          active={tab === "schedule"}
          onClick={() => setTabAndUrl("schedule")}
          icon={<Calendar className="w-3.5 h-3.5" />}
          label="Schedule"
        />
        <TabButton
          active={tab === "scheduled"}
          onClick={() => setTabAndUrl("scheduled")}
          icon={<Calendar className="w-3.5 h-3.5" />}
          label="Scheduled"
        />
        <TabButton
          active={tab === "published"}
          onClick={() => setTabAndUrl("published")}
          icon={<Send className="w-3.5 h-3.5" />}
          label="Published"
        />
        <TabButton
          active={tab === "publishing"}
          onClick={() => setTabAndUrl("publishing")}
          icon={<Loader2 className="w-3.5 h-3.5" />}
          label="Publishing"
        />
        <TabButton
          active={tab === "comments"}
          onClick={() => setTabAndUrl("comments")}
          icon={<MessageCircle className="w-3.5 h-3.5" />}
          label="Comments"
          dot={pendingHint}
        />
      </div>

      {tab === "schedule" && <ScheduleTab />}
      {tab === "scheduled" && <ScheduledTab />}
      {tab === "publishing" && <PublishingTab />}
      {tab === "published" && <PublishedTab />}
      {tab === "comments" && <CommentsTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
  dot,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  dot?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition",
        active
          ? "bg-white/10 text-white"
          : "text-white/50 hover:text-white/80",
      )}
    >
      {icon}
      {label}
      {dot && (
        <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-amber-400" />
      )}
    </button>
  );
}

// ── Schedule tab ─────────────────────────────────────────────────────────

function ScheduleTab() {
  // CHANGE 4 — fetch fully-rendered casts so the user can publish or
  // schedule them straight from this hub. Previously the only path here
  // was via per-cast /publish/:id; now PublishCard sits inline.
  const { data: readyData, isLoading } = useQuery({
    queryKey: ["ready-casts"],
    queryFn: () => castsApi.list({ status: "ready", has_render: true, include_clips: true }),
  });

  const readyCasts: any[] = (readyData as any)?.casts || [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-white/40 text-sm">
        <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading…
      </div>
    );
  }

  if (readyCasts.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center text-sm text-white/50">
        Nothing ready to publish yet.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xs font-medium text-white/50 mb-3 uppercase tracking-wider">
          Ready to Publish
        </h2>
        <div className="space-y-3 mb-6">
          {readyCasts.map((c) => (
            <PublishCard key={c.id} cast={c} />
          ))}
        </div>
      </div>
    </div>
  );
}

function CalendarStrip({
  selectedDate,
  onChange,
  scheduledDates,
}: {
  selectedDate: Date;
  onChange: (d: Date) => void;
  scheduledDates: Date[];
}) {
  const days = useMemo(() => {
    const out: Date[] = [];
    const start = new Date(selectedDate);
    start.setDate(start.getDate() - 3);
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      out.push(d);
    }
    return out;
  }, [selectedDate]);

  return (
    <div className="flex items-center gap-1 bg-white/5 p-2 rounded-xl">
      <button
        onClick={() => onChange(addDays(selectedDate, -7))}
        className="p-1 text-white/30 hover:text-white/70"
        title="Previous week"
      >
        <ChevronLeft className="w-4 h-4" />
      </button>

      <div className="flex flex-1 items-center justify-around gap-1">
        {days.map((d) => {
          const today = isSameDay(d, new Date());
          const sel = isSameDay(d, selectedDate);
          const has = scheduledDates.some((x) => isSameDay(x, d));
          return (
            <button
              key={d.toISOString()}
              onClick={() => onChange(d)}
              className={cn(
                "flex flex-col items-center px-3 py-1.5 rounded-lg min-w-[52px] transition-colors",
                sel
                  ? "bg-accent/20 text-accent ring-1 ring-accent/40"
                  : "hover:bg-white/5",
              )}
            >
              <span className="text-[10px] text-white/40 uppercase tracking-wider">
                {d.toLocaleDateString(undefined, { weekday: "short" })}
              </span>
              <span
                className={cn(
                  "text-sm font-medium mt-0.5",
                  today && !sel && "text-accent",
                )}
              >
                {d.getDate()}
              </span>
              {has && (
                <div className="w-1.5 h-1.5 rounded-full bg-accent mt-1" />
              )}
            </button>
          );
        })}
      </div>

      <button
        onClick={() => onChange(addDays(selectedDate, 7))}
        className="p-1 text-white/30 hover:text-white/70"
        title="Next week"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  );
}

function ScheduledPostCard({ post }: { post: any }) {
  const navigate = useNavigate();
  const platforms = post.platforms || [];
  return (
    <div className="p-3.5 bg-white/[0.04] border border-white/10 rounded-xl">
      <div className="flex items-start gap-3">
        <div className="w-12 h-20 rounded-lg bg-white/10 flex items-center justify-center shrink-0 overflow-hidden">
          {post.media_url ? (
            <MediaThumb url={post.media_url} className="w-full h-full object-cover" />
          ) : (
            <Send className="w-4 h-4 text-white/30" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-white truncate">
            {post.cast_id || "Untitled cast"}
          </div>
          <p className="text-xs text-white/50 mt-0.5 line-clamp-2">{post.caption}</p>
          <div className="flex items-center gap-2 mt-2">
            {platforms.map((pl: any, i: number) => (
              <div
                key={i}
                className="flex items-center gap-1.5 text-[11px] text-white/60"
              >
                <PlatformIcon platform={pl.platform} className="w-4 h-4" />
                <span>{platformLabel(pl.platform)}</span>
              </div>
            ))}
            {post.scheduled_for && (
              <>
                <span className="text-white/20">·</span>
                <span className="text-[11px] text-white/40">
                  {new Date(post.scheduled_for).toLocaleTimeString([], {
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => navigate(`/publish/${post.cast_id}?post_id=${post.id}`)}
            title="Edit"
          >
            <Pencil className="w-3.5 h-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              if (!(await confirmAction({
                title: "Cancel this scheduled post?",
                confirmButtonText: "Cancel post",
                cancelButtonText: "Keep it",
              }))) return;
              try {
                await socialApi.deletePost(post.id);
                toast({ title: "Cancelled" });
              } catch (err: any) {
                toast({
                  title: "Could not cancel",
                  description: err?.response?.data?.detail || err.message,
                  variant: "destructive",
                });
              }
            }}
            className="text-red-400 hover:text-red-300"
            title="Cancel"
          >
            <X className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Publish card (CHANGE 4) ──────────────────────────────────────────────
// Surfaces a fully-rendered cast inside the Schedule tab so the user can
// pick platforms / write a caption / hit Publish without leaving the hub.
// AI-generated caption + hashtags are out of scope for this PR — the
// user types into an empty textarea. The per-cast /publish/:id page
// (which does call socialApi.generateCaption) remains the deeper editor.
function PublishCard({ cast }: { cast: any }) {
  const navigate = useNavigate();
  const [platforms, setPlatforms] = useState<string[]>(["tiktok", "instagram_reels"]);
  const [scheduleMode, setScheduleMode] = useState<"now" | "later">("now");
  const [scheduleDate, setScheduleDate] = useState<string>("");
  // datetime-local has no timezone — offset by the local UTC offset so
  // `min` actually matches what `new Date(scheduleDate)` treats as "now"
  // on the /publish/:id page this hands off to.
  const minScheduleValue = useMemo(() => {
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    return now.toISOString().slice(0, 16);
  }, []);
  // TODO future: AI-generated caption + hashtags — until then the user
  // types these manually. The per-cast /publish/:id page already calls
  // socialApi.generateCaption; when we lift it here we'll prefill these.
  const [caption, setCaption] = useState("");
  const approvedClips: { name?: string; duration_seconds?: number | null }[] =
    cast.approved_clips || [];

  const togglePlatform = (p: string) =>
    setPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));

  const goPublish = () => {
    // The per-cast Publish page is where the actual posting happens;
    // wiring socialApi.publishPost from here would duplicate the OAuth
    // / channel-resolution dance that page already does. We send the
    // user there with their selections preserved via query params.
    const q = new URLSearchParams();
    if (platforms.length) q.set("platforms", platforms.join(","));
    q.set("mode", scheduleMode);
    if (scheduleMode === "later" && scheduleDate) q.set("at", scheduleDate);
    if (caption) q.set("caption", caption);
    navigate(`/publish/${cast.id}?${q.toString()}`);
  };

  // Resolved by the casts list endpoint (default avatar look face →
  // avatar face fallback). Shown so the user can tell at a glance which
  // avatar is acting in this cast.
  const avatarThumb: string | null = cast.avatar_thumbnail_url || null;
  const avatarName: string = cast.avatar_name || "Avatar";
  console.log(cast)

  return (
    <div className="p-4 bg-white/[0.03] border border-white/[0.07] rounded-xl">
      <div className="flex gap-4 mb-4">
        <div className="w-20 h-36 rounded-lg overflow-hidden bg-white/5 flex-shrink-0 relative">
          {cast.final_video_url ? (
            <MediaThumb url={cast.final_video_url} className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <Film className="w-6 h-6 text-white/10" />
            </div>
          )}
          {avatarThumb && (
            <div
              className="absolute bottom-1 right-1 w-7 h-7 rounded-full overflow-hidden ring-2 ring-black/60 bg-white/5"
              title={avatarName}
            >
              <img src={avatarThumb} alt={avatarName} className="w-full h-full object-cover" />
            </div>
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{cast.name || "Untitled cast"}</div>
          <div className="text-[11px] text-white/30 mt-0.5">
            {avatarName !== "Avatar" ? `${avatarName} · ` : ""}
            {cast.total_clips ? `${cast.total_clips} blocks` : ""}
            {cast.final_video_url ? " · Rendered" : ""}
          </div>

          {approvedClips.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {approvedClips.map((clip, i) => (
                <span
                  key={`${clip.name}-${i}`}
                  className="text-[9px] px-1.5 py-0.5 bg-accent/10 text-accent rounded-full inline-flex items-center gap-1"
                  title="Approved clip"
                >
                  📱 {clip.name}
                  {clip.duration_seconds ? ` (${clip.duration_seconds}s)` : ""}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        {["tiktok", "instagram_reels", "youtube_shorts", "facebook", "linkedin"].map((p) => (
          <button
            key={p}
            onClick={() => togglePlatform(p)}
            className={cn(
              "text-[10px] px-2.5 py-1 rounded-full border transition-all",
              platforms.includes(p)
                ? "bg-accent/20 border-accent/40 text-accent"
                : "border-white/10 text-white/30 hover:border-white/20",
            )}
          >
            {p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
          </button>
        ))}
      </div>

      <textarea
        value={caption}
        onChange={(e) => setCaption(e.target.value)}
        placeholder="Write a caption…"
        rows={2}
        className="w-full text-xs bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white/80 resize-none mb-3"
      />

      <div className="flex items-center gap-3 mb-4">
        <div className="flex gap-1 bg-white/5 rounded-lg p-0.5">
          <button
            onClick={() => setScheduleMode("now")}
            className={cn(
              "text-[10px] px-3 py-1 rounded",
              scheduleMode === "now" ? "bg-accent text-white" : "text-white/50",
            )}
          >
            Now
          </button>
          <button
            onClick={() => setScheduleMode("later")}
            className={cn(
              "text-[10px] px-3 py-1 rounded",
              scheduleMode === "later" ? "bg-accent text-white" : "text-white/50",
            )}
          >
            Schedule
          </button>
        </div>
        {scheduleMode === "later" && (
          <input
            type="datetime-local"
            value={scheduleDate}
            min={minScheduleValue}
            onChange={(e) => setScheduleDate(e.target.value)}
            className="text-xs bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-white/70"
          />
        )}
      </div>

      <div className="flex gap-2">
        <Button onClick={goPublish} className="bg-accent hover:bg-accent/90">
          {scheduleMode === "now" ? (
            <>
              <Send className="w-3.5 h-3.5 mr-1.5" /> Publish Now
            </>
          ) : (
            <>
              <Calendar className="w-3.5 h-3.5 mr-1.5" /> Schedule
            </>
          )}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate(`/cast-builder/${cast.id}`)}
        >
          <Pencil className="w-3 h-3 mr-1" /> Edit
        </Button>
      </div>
    </div>
  );
}


// ── Scheduled tab ────────────────────────────────────────────────────────

function ScheduledTab() {
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const navigate = useNavigate();

  const { data: posts, isLoading } = useQuery({
    queryKey: ["social-posts", "scheduled"],
    queryFn: () => socialApi.listPosts({ status: "scheduled" }),
  });

  const scheduled = (posts || []).filter(
    (p) => p.status === "scheduled" && p.scheduled_for,
  );

  const scheduledDates = useMemo(() => {
    return scheduled.map((p) => new Date(p.scheduled_for));
  }, [scheduled]);

  const postsForDay = scheduled.filter((p) =>
    isSameDay(new Date(p.scheduled_for), selectedDate),
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10 text-white/40 text-sm">
        <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <CalendarStrip
        selectedDate={selectedDate}
        onChange={setSelectedDate}
        scheduledDates={scheduledDates}
      />

      {postsForDay.length > 0 ? (
        <div className="space-y-2">
          {postsForDay.map((p) => (
            <ScheduledPostCard key={p.id} post={p} />
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <Calendar className="w-9 h-9 mx-auto mb-3 opacity-30" />
          <p className="text-sm text-white/60 mb-3">
            Nothing scheduled for {formatHumanDate(selectedDate)}.
          </p>
          <Button
            size="sm"
            onClick={() => navigate("/cast-builder")}
            className="bg-accent hover:bg-accent/90"
          >
            <Plus className="w-3.5 h-3.5 mr-1.5" /> Build a cast
          </Button>
        </div>
      )}
    </div>
  );
}


// ── Publishing tab ───────────────────────────────────────────────────────

function PublishingTab() {
  const { data: posts, isLoading } = useQuery({
    queryKey: ["social-posts", "publishing"],
    queryFn: () => socialApi.listPosts({ status: "publishing" }),
    // Posts actually move through this state — poll while any are visible
    // so it self-clears without a manual refresh once Zernio finishes.
    refetchInterval: 5000,
  });

  const sorted = useMemo(
    () =>
      [...(posts || [])].sort(
        (a, b) =>
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      ),
    [posts],
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10 text-white/40 text-sm">
        <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading…
      </div>
    );
  }

  if (sorted.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center text-sm text-white/50">
        Nothing publishing right now.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {sorted.map((p) => (
        <ScheduledPostCard key={p.id} post={p} />
      ))}
    </div>
  );
}


// ── Published tab ────────────────────────────────────────────────────────

function PublishedTab() {
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [windowFilter, setWindowFilter] = useState<"7d" | "30d" | "all">("30d");

  const { data: posts, refetch, isLoading } = useQuery({
    queryKey: ["social-posts"],
    queryFn: () => socialApi.listPosts(),
  });

  const filtered = useMemo(() => {
    const list = (posts || []).filter((p) => p.status === "published");
    return list.filter((p) => {
      if (platformFilter !== "all") {
        const ok = (p.platforms || []).some(
          (pl: any) => pl.platform === platformFilter,
        );
        if (!ok) return false;
      }
      if (windowFilter !== "all" && p.published_at) {
        const days = windowFilter === "7d" ? 7 : 30;
        const cutoff = Date.now() - days * 24 * 3600 * 1000;
        if (new Date(p.published_at).getTime() < cutoff) return false;
      }
      return true;
    });
  }, [posts, platformFilter, windowFilter]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs">
        <select
          value={platformFilter}
          onChange={(e) => setPlatformFilter(e.target.value)}
          className="bg-white/5 border border-white/10 rounded-md px-2 py-1 text-white/70"
        >
          {/* The select's own text-white/70 only styles its closed-state
              button — the browser renders the opened <option> popup with
              its own default (usually white) background, which options
              ignore parent Tailwind classes for. Without an explicit color
              here, light theme text on that white popup is invisible until
              :hover's browser-native highlight creates contrast again. */}
          <option value="all" className="bg-neutral-900 text-white">All platforms</option>
          <option value="tiktok" className="bg-neutral-900 text-white">TikTok</option>
          <option value="instagram" className="bg-neutral-900 text-white">Instagram</option>
          <option value="youtube" className="bg-neutral-900 text-white">YouTube</option>
          <option value="linkedin" className="bg-neutral-900 text-white">LinkedIn</option>
          <option value="facebook" className="bg-neutral-900 text-white">Facebook</option>
        </select>
        <select
          value={windowFilter}
          onChange={(e) => setWindowFilter(e.target.value as any)}
          className="bg-white/5 border border-white/10 rounded-md px-2 py-1 text-white/70"
        >
          <option value="7d" className="bg-neutral-900 text-white">Last 7 days</option>
          <option value="30d" className="bg-neutral-900 text-white">Last 30 days</option>
          <option value="all" className="bg-neutral-900 text-white">All time</option>
        </select>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-10 text-white/40 text-sm">
          <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading…
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center text-sm text-white/50">
          No published posts in this window.
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((p) => (
            <PublishedPostCard key={p.id} post={p} onChanged={refetch} />
          ))}
        </div>
      )}
    </div>
  );
}

function PublishedPostCard({ post, onChanged }: { post: any; onChanged: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="p-3.5 bg-white/[0.04] border border-white/10 rounded-xl">
      <div className="flex items-start gap-3">
        <div className="w-12 h-20 rounded-lg bg-white/10 flex items-center justify-center shrink-0 overflow-hidden">
          {post.media_url ? (
            <MediaThumb url={post.media_url} className="w-full h-full object-cover" />
          ) : (
            <Send className="w-4 h-4 text-white/30" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium text-white truncate">
              {post.cast_id || "Untitled cast"}
            </div>
            <span className="text-[11px] text-white/30 shrink-0">
              {post.published_at
                ? new Date(post.published_at).toLocaleString()
                : "—"}
            </span>
          </div>
          <p className="text-xs text-white/50 mt-0.5 line-clamp-2">{post.caption}</p>
          <div className="flex items-center gap-3 mt-2 flex-wrap">
            {(post.platforms || []).map((pl: any, i: number) => (
              <div key={i} className="flex items-center gap-1.5 text-[11px] text-white/60">
                <PlatformIcon platform={pl.platform} className="w-4 h-4" />
                {platformLabel(pl.platform)}
              </div>
            ))}
          </div>
          {post.analytics && Object.keys(post.analytics).length > 0 && (
            <div className="grid grid-cols-4 gap-2 mt-3 pt-2 border-t border-white/[0.06]">
              {["views", "likes", "comments", "shares"].map((k) => (
                <div key={k} className="text-center">
                  <p className="text-[10px] text-white/40 uppercase tracking-wider">{k}</p>
                  <p className="text-sm text-white tabular-nums">{post.analytics[k] ?? "—"}</p>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => navigate(`/comments/${post.id}`)}
            title="View comments"
          >
            <MessageCircle className="w-3.5 h-3.5" />
          </Button>
          {post.media_url && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => window.open(post.media_url, "_blank")}
              title="Open media"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              if (!(await confirmAction({
                title: "Delete this post?",
                text: "This also removes it on every platform it was posted to.",
                confirmButtonText: "Delete",
              }))) return;
              try {
                await socialApi.deletePost(post.id);
                toast({ title: "Post deleted" });
                onChanged();
              } catch (err: any) {
                toast({
                  title: "Could not delete",
                  description: err?.response?.data?.detail || err.message,
                  variant: "destructive",
                });
              }
            }}
            className="text-red-400 hover:text-red-300"
            title="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Comments tab ─────────────────────────────────────────────────────────

function CommentsTab() {
  const [filter, setFilter] = useState<"all" | "pending" | "replied" | "skipped" | "flagged">(
    "pending",
  );

  const { data: posts } = useQuery({
    queryKey: ["social-posts"],
    queryFn: () => socialApi.listPosts(),
  });
  const publishedPostIds = (posts || [])
    .filter((p) => p.status === "published")
    .map((p) => p.id);

  // Bulk-fetch comments for every published post. useQueries gives us a
  // stable hook count regardless of how many posts the user has, so
  // Rules of Hooks holds.
  const commentQueries = useQueries({
    queries: publishedPostIds.map((id) => ({
      queryKey: ["social-comments", id],
      queryFn: () => socialApi.getComments(id),
      enabled: !!id,
    })),
  });

  const allComments: Array<{ post: any; comment: any }> = useMemo(() => {
    const out: Array<{ post: any; comment: any }> = [];
    publishedPostIds.forEach((id, i) => {
      const post = (posts || []).find((p) => p.id === id);
      const list = commentQueries[i]?.data;
      if (!post || !Array.isArray(list)) return;
      list.forEach((c: any) => out.push({ post, comment: c }));
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [posts, commentQueries.map((q) => q.dataUpdatedAt).join(",")]);

  const counts = {
    pending: allComments.filter((x) => x.comment.reply_status === "pending").length,
    replied: allComments.filter((x) =>
      ["sent", "approved"].includes(x.comment.reply_status),
    ).length,
    skipped: allComments.filter((x) => x.comment.reply_status === "skipped").length,
    flagged: allComments.filter((x) => x.comment.is_prompt_injection).length,
  };

  const visible = allComments.filter(({ comment }) => {
    if (filter === "all") return true;
    if (filter === "pending") return comment.reply_status === "pending" && !comment.is_prompt_injection;
    if (filter === "replied") return ["sent", "approved"].includes(comment.reply_status);
    if (filter === "skipped") return comment.reply_status === "skipped";
    if (filter === "flagged") return comment.is_prompt_injection;
    return true;
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <FilterPill active={filter === "pending"} onClick={() => setFilter("pending")} color="amber" label={`Needs reply · ${counts.pending}`} />
        <FilterPill active={filter === "replied"} onClick={() => setFilter("replied")} color="green" label={`Replied · ${counts.replied}`} />
        <FilterPill active={filter === "skipped"} onClick={() => setFilter("skipped")} color="gray" label={`Skipped · ${counts.skipped}`} />
        <FilterPill active={filter === "flagged"} onClick={() => setFilter("flagged")} color="red" label={`Flagged · ${counts.flagged}`} />
        <FilterPill active={filter === "all"} onClick={() => setFilter("all")} color="white" label={`All · ${allComments.length}`} />
      </div>

      {visible.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center text-sm text-white/50">
          {filter === "pending"
            ? "All caught up — no comments waiting."
            : "No comments to show."}
        </div>
      ) : (
        <div className="space-y-2">
          {visible.map(({ post, comment }) => (
            <CommentCard key={comment.id} post={post} comment={comment} />
          ))}
        </div>
      )}
    </div>
  );
}

function FilterPill({
  active,
  onClick,
  color,
  label,
}: {
  active: boolean;
  onClick: () => void;
  color: "amber" | "green" | "gray" | "red" | "white";
  label: string;
}) {
  const COLORS: Record<string, string> = {
    amber: "bg-amber-500/15 text-amber-200 border-amber-400/30",
    green: "bg-green-500/15 text-green-200 border-green-400/30",
    gray: "bg-white/5 text-white/40 border-white/10",
    red: "bg-red-500/15 text-red-200 border-red-400/30",
    white: "bg-white/10 text-white border-white/20",
  };
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-[11px] font-medium transition",
        active ? COLORS[color] : "bg-transparent text-white/40 border-white/10 hover:text-white/70",
      )}
    >
      {label}
    </button>
  );
}

function CommentCard({ post, comment }: { post: any; comment: any }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(comment.ai_suggested_reply || "");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    setBusy(true);
    try {
      await socialApi.reply(post.id, comment.id, draft);
      toast({ title: "Reply sent", variant: "success" });
    } catch (err: any) {
      toast({
        title: "Reply failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const skip = async () => {
    setBusy(true);
    try {
      await socialApi.skip(post.id, comment.id);
      toast({ title: "Skipped" });
    } catch (err: any) {
      toast({
        title: "Skip failed",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={cn(
        "p-3 border rounded-xl",
        comment.is_prompt_injection
          ? "border-red-500/30 bg-red-500/5"
          : "border-white/10 bg-white/[0.03]",
      )}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <PlatformIcon platform={comment.platform || "tiktok"} className="w-4 h-4" />
        <span className="text-[11px] font-medium text-white/70">
          @{comment.author_handle || comment.author_name || "unknown"}
        </span>
        <span className="text-[10px] text-white/25">
          {comment.created_at ? formatRelativeTime(new Date(comment.created_at)) : ""}
        </span>
      </div>

      <p className="text-sm text-white/85 mb-2">"{comment.text}"</p>

      {comment.is_prompt_injection ? (
        <div className="flex items-center gap-2 text-xs text-red-300">
          <ShieldAlert className="w-3.5 h-3.5" /> Prompt injection detected — auto-skipped
        </div>
      ) : comment.ai_suggested_reply || draft ? (
        <div className="space-y-2">
          <div className="text-[10px] text-white/30 uppercase tracking-wider flex items-center gap-1">
            <Sparkles className="w-3 h-3 text-accent" /> AI suggestion
          </div>
          {editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-lg p-2 text-sm text-white/80 resize-none focus:outline-none focus:border-accent/50"
              rows={2}
            />
          ) : (
            <p className="text-sm text-accent/90 bg-accent/5 rounded-lg px-3 py-2 border border-accent/15">
              {draft}
            </p>
          )}
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={busy || !draft.trim()} onClick={send}>
              <Check className="w-3 h-3 mr-1" /> Send
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setEditing((v) => !v)}
            >
              <Pencil className="w-3 h-3 mr-1" /> {editing ? "Done" : "Edit"}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={skip}>
              Skip
            </Button>
          </div>
        </div>
      ) : (
        <div className="text-xs text-white/40">No suggestion yet.</div>
      )}
    </div>
  );
}

// ── tiny date utils ─────────────────────────────────────────────────────

function isSameDay(a: Date, b: Date) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}
function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}
function formatHumanDate(d: Date): string {
  const today = new Date();
  if (isSameDay(d, today)) return "today";
  const tomorrow = addDays(today, 1);
  if (isSameDay(d, tomorrow)) return "tomorrow";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}
function formatRelativeTime(d: Date): string {
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return d.toLocaleDateString();
}
