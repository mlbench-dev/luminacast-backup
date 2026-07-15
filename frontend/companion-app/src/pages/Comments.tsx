import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  MessageSquare, Check, X, Edit, Loader2, ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { socialApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";

/**
 * Comments manager.
 *
 * Two routes:
 *   /comments              \u2192 list of all your published posts (pick one)
 *   /comments/:postId      \u2192 the comment-by-comment review UI
 *
 * The review UI shows AI-suggested replies for each comment with three
 * actions: Send, Edit, Skip. Prompt-injection attempts are auto-flagged and
 * rendered as a no-op row instead of letting them through to the LLM.
 */
export default function Comments() {
  const { postId } = useParams<{ postId?: string }>();
  return postId ? <PostCommentsView postId={postId} /> : <PostsListView />;
}


function PostsListView() {
  const navigate = useNavigate();
  const { data: posts } = useQuery({
    queryKey: ["social-posts"],
    queryFn: () => socialApi.listPosts(),
  });

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-4">
      <div className="flex items-center gap-2">
        <MessageSquare className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-white">Comments</h1>
      </div>
      {!posts || posts.length === 0 ? (
        <div className="rounded-md border border-dashed border-white/10 p-8 text-center text-sm text-white/50">
          No published posts yet.
        </div>
      ) : (
        <ul className="space-y-2">
          {posts.map((p) => (
            <li
              key={p.id}
              onClick={() => navigate(`/comments/${p.id}`)}
              className="rounded-xl border border-white/10 bg-white/[0.03] p-4 cursor-pointer hover:border-white/20"
            >
              <p className="text-sm text-white line-clamp-1">{p.caption || p.id}</p>
              <p className="text-xs text-white/40 mt-1">
                {(p.platforms || []).map((pl: any) => pl.platform).join(" · ")}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function PostCommentsView({ postId }: { postId: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: comments, isLoading } = useQuery({
    queryKey: ["social-comments", postId],
    queryFn: () => socialApi.getComments(postId),
    refetchInterval: 30000,
  });

  const stats = (comments || []).reduce(
    (acc, c: any) => {
      acc.total += 1;
      if (c.reply_status === "sent") acc.replied += 1;
      else if (c.reply_status === "skipped") acc.skipped += 1;
      else if (c.reply_status === "flagged" || c.is_prompt_injection) acc.flagged += 1;
      return acc;
    },
    { total: 0, replied: 0, skipped: 0, flagged: 0 },
  );

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-5 w-5 text-accent" />
          <h1 className="text-xl font-semibold text-white">Comments</h1>
        </div>
        <Button size="sm" variant="outline" onClick={() => navigate("/comments")}>
          Back to list
        </Button>
      </div>

      <div className="text-xs text-white/50">
        {stats.total} total · {stats.replied} replied · {stats.skipped} skipped · {stats.flagged} flagged
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-white/50">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : !comments || comments.length === 0 ? (
        <div className="rounded-md border border-dashed border-white/10 p-8 text-center text-sm text-white/50">
          No comments yet. Comments are pulled from Zernio every 5 minutes.
        </div>
      ) : (
        <ul className="space-y-3">
          {comments.map((c: any) => (
            <CommentRow
              key={c.id}
              postId={postId}
              comment={c}
              onChanged={() =>
                qc.invalidateQueries({ queryKey: ["social-comments", postId] })
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}


function CommentRow({
  postId, comment, onChanged,
}: { postId: string; comment: any; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.ai_suggested_reply || "");
  const [busy, setBusy] = useState(false);

  if (comment.is_prompt_injection || comment.reply_status === "flagged") {
    return (
      <li className="rounded-xl border border-yellow-500/30 bg-yellow-500/5 p-4 space-y-1">
        <PlatformAndAuthor c={comment} />
        <p className="text-sm text-white/90">{comment.text}</p>
        <p className="text-xs text-yellow-300 flex items-center gap-1">
          <ShieldAlert className="h-3.5 w-3.5" />
          Prompt-injection detected — auto-skipped.
        </p>
      </li>
    );
  }

  const sent = comment.reply_status === "sent";
  const skipped = comment.reply_status === "skipped";

  const send = async (text?: string) => {
    setBusy(true);
    try {
      await socialApi.reply(postId, comment.id, text);
      toast({ title: "Reply sent" });
      onChanged();
    } catch (err: any) {
      toast({
        title: "Could not send",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
      setEditing(false);
    }
  };

  const skip = async () => {
    setBusy(true);
    try {
      await socialApi.skip(postId, comment.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-2">
      <PlatformAndAuthor c={comment} />
      <p className="text-sm text-white/90">{comment.text}</p>

      {sent ? (
        <p className="text-xs text-green-300 flex items-center gap-1">
          <Check className="h-3.5 w-3.5" /> Replied: {comment.actual_reply}
        </p>
      ) : skipped ? (
        <p className="text-xs text-white/40">Skipped.</p>
      ) : comment.ai_suggested_reply ? (
        <>
          {editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={2}
              className="w-full rounded-md bg-white/5 border border-white/10 px-3 py-2 text-xs text-white"
            />
          ) : (
            <p className="text-xs text-accent/90">
              <span className="text-white/30">AI:</span> {comment.ai_suggested_reply}
            </p>
          )}
          <div className="flex gap-1.5 pt-1">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => (editing ? send(draft) : send())}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="h-3.5 w-3.5 mr-1" />
              )}
              Send
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setEditing((v) => !v)}
            >
              <Edit className="h-3.5 w-3.5 mr-1" /> {editing ? "Cancel edit" : "Edit"}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={skip}>
              <X className="h-3.5 w-3.5 mr-1" /> Skip
            </Button>
          </div>
        </>
      ) : (
        <p className="text-xs text-white/40">No AI suggestion available.</p>
      )}
    </li>
  );
}


function PlatformAndAuthor({ c }: { c: any }) {
  return (
    <div className="flex items-center gap-2 text-[10px] uppercase">
      <span className="px-2 py-0.5 rounded bg-white/[0.06] text-white/70">
        {c.platform || "?"}
      </span>
      <span className="text-white/40">
        {c.author_handle || c.author_name || "anon"} ·{" "}
        {c.created_at
          ? new Date(c.created_at).toLocaleString()
          : "—"}
      </span>
    </div>
  );
}
