import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Send, ExternalLink, MessageSquare, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { socialApi } from "@/lib/api";
import { toast } from "@/hooks/useToast";

/**
 * Published Posts page \u2014 list view of all SocialPosts the user has created
 * via Zernio. Filter / status indicators / link to the Comments view per
 * post.
 */
export default function Published() {
  const navigate = useNavigate();
  const { data: posts, refetch } = useQuery({
    queryKey: ["social-posts"],
    queryFn: () => socialApi.listPosts(),
  });

  const handleDelete = async (postId: string) => {
    if (!window.confirm("Delete this post (also removes it on every platform)?")) return;
    try {
      await socialApi.deletePost(postId);
      toast({ title: "Post deleted" });
      refetch();
    } catch (err: any) {
      toast({
        title: "Could not delete",
        description: err?.response?.data?.detail || err.message,
        variant: "destructive",
      });
    }
  };

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Send className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-white">Published Posts</h1>
      </div>

      {!posts || posts.length === 0 ? (
        <div className="rounded-md border border-dashed border-white/10 p-8 text-center text-sm text-white/50">
          You haven't published anything yet. Render a cast and click Publish.
        </div>
      ) : (
        <ul className="space-y-3">
          {posts.map((p) => (
            <li
              key={p.id}
              className="rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={p.status} />
                    <span className="text-xs text-white/40">
                      {p.published_at
                        ? `Posted ${new Date(p.published_at).toLocaleString()}`
                        : p.scheduled_for
                          ? `Scheduled ${new Date(p.scheduled_for).toLocaleString()}`
                          : `Created ${new Date(p.created_at).toLocaleString()}`}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-white/90 line-clamp-2">{p.caption}</p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {(p.platforms || []).map((pl: any, i: number) => (
                      <span
                        key={i}
                        className="text-[10px] px-2 py-0.5 rounded bg-white/[0.06] text-white/70"
                      >
                        {pl.platform}
                      </span>
                    ))}
                  </div>
                  {p.error_message && (
                    <p className="text-xs text-red-300 mt-1">{p.error_message}</p>
                  )}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => navigate(`/comments/${p.id}`)}
                  >
                    <MessageSquare className="h-3.5 w-3.5 mr-1" /> Comments
                  </Button>
                  {p.media_url && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => window.open(p.media_url, "_blank")}
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleDelete(p.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>

              {p.analytics && Object.keys(p.analytics).length > 0 && (
                <div className="grid grid-cols-4 gap-2 pt-2 border-t border-white/[0.06]">
                  {["views", "likes", "comments", "shares"].map((k) => (
                    <div key={k} className="text-center">
                      <p className="text-[10px] text-white/40 uppercase">{k}</p>
                      <p className="text-sm text-white">{p.analytics[k] ?? "—"}</p>
                    </div>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    draft: "bg-white/10 text-white/60",
    scheduled: "bg-blue-500/15 text-blue-300",
    publishing: "bg-yellow-500/15 text-yellow-300",
    published: "bg-green-500/15 text-green-300",
    failed: "bg-red-500/15 text-red-300",
    deleted: "bg-white/10 text-white/40 line-through",
  };
  const cls = map[status] || "bg-white/10 text-white/60";
  return (
    <span className={`text-[10px] uppercase px-2 py-0.5 rounded ${cls}`}>
      {status}
    </span>
  );
}
