import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, Check, Undo2, Loader2, Eye } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";
import { castsApi, extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { TeamRole } from "@/lib/types";

/**
 * Publisher's review queue — casts a Creator has submitted, awaiting
 * approval before they can be scheduled/published. Mirrors the
 * one-status-field workflow enforced server-side in routers/casts.py
 * (submit-for-review / approve / reject-review) and the Zernio gate in
 * routers/social.py::create_social_post — this page is purely a
 * front-end for actions the backend already requires regardless of UI.
 */
export function ReviewQueuePage() {
  const navigate = useNavigate();
  const hasTeamRole = useAuthStore((s) => s.hasTeamRole);
  const canReview = hasTeamRole(TeamRole.PUBLISHER);

  const queryClient = useQueryClient();
  const { data: casts = [], isLoading } = useQuery({
    queryKey: ["review-queue"],
    queryFn: () => castsApi.reviewQueue(),
    enabled: canReview,
  });

  const [rejectingId, setRejectingId] = useState<string | null>(null);

  const approveMutation = useMutation({
    mutationFn: (castId: string) => castsApi.approveCast(castId),
    onSuccess: () => {
      toast({ title: "Approved", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    },
    onError: (err: unknown) => {
      toast({
        title: "Could not approve",
        description: extractErrorMessage(err, "Something went wrong."),
        variant: "destructive",
      });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ castId, reason }: { castId: string; reason?: string }) =>
      castsApi.rejectReview(castId, reason),
    onSuccess: () => {
      toast({ title: "Sent back to draft" });
      queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    },
    onError: (err: unknown) => {
      toast({
        title: "Could not send back",
        description: extractErrorMessage(err, "Something went wrong."),
        variant: "destructive",
      });
    },
    onSettled: () => setRejectingId(null),
  });

  const handleReject = async (castId: string) => {
    const confirmed = await confirmAction({
      title: "Send this cast back to the Creator?",
      text: "It returns to draft — they'll need to resubmit once it's fixed.",
      confirmButtonText: "Send back",
    });
    if (!confirmed) return;
    setRejectingId(castId);
    rejectMutation.mutate({ castId });
  };

  if (!canReview) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-6">
        <h1 className="text-xl font-semibold text-white mb-2">Review Queue</h1>
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center text-sm text-white/50">
          Only Publishers and the workspace owner can review submitted casts.
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <ClipboardCheck className="w-5 h-5 text-accent" /> Review Queue
        </h1>
        <p className="text-xs text-white/40 mt-0.5">
          Casts Creators have submitted — approve before they can be scheduled or published.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-10 text-white/40 text-sm">
          <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading…
        </div>
      ) : casts.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center text-sm text-white/50">
          Nothing waiting for review.
        </div>
      ) : (
        <ul className="space-y-3">
          {casts.map((c) => (
            <li key={c.id} className="rounded-xl border border-white/10 bg-white/[0.04] p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium text-white truncate">{c.name || "Untitled cast"}</div>
                  {c.description && (
                    <p className="text-xs text-white/50 mt-0.5 line-clamp-2">{c.description}</p>
                  )}
                  <p className="text-[11px] text-white/30 mt-1.5">
                    Submitted by {c.submitted_by_name || "someone"}
                    {c.submitted_for_review_at &&
                      ` · ${new Date(c.submitted_for_review_at).toLocaleString()}`}
                  </p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => navigate(`/cast-builder/${c.id}`)}
                    title="View cast"
                  >
                    <Eye className="w-3.5 h-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleReject(c.id)}
                    disabled={rejectingId === c.id}
                  >
                    <Undo2 className="w-3.5 h-3.5 mr-1.5" /> Send back
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => approveMutation.mutate(c.id)}
                    disabled={approveMutation.isPending}
                    className="bg-accent hover:bg-accent/90"
                  >
                    <Check className="w-3.5 h-3.5 mr-1.5" /> Approve
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
