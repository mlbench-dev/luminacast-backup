import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Users, Loader2, Plus, X } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "@/hooks/useToast";
import { confirmAction } from "@/lib/swal";
import { cn } from "@/lib/cn";
import { teamsApi, extractErrorMessage, type TeamMemberDto } from "@/lib/api";
import { TeamRole } from "@/lib/types";
import { useAuthStore } from "@/stores/authStore";

const ROLE_LABELS: Record<TeamRole, string> = {
  [TeamRole.VIEWER]: "Viewer",
  [TeamRole.CREATOR]: "Creator",
  [TeamRole.PUBLISHER]: "Publisher",
};

const ROLE_DESCRIPTIONS: Record<TeamRole, string> = {
  [TeamRole.VIEWER]: "Sees everything, changes nothing",
  [TeamRole.CREATOR]: "Creates avatars, products, casts — can't publish",
  [TeamRole.PUBLISHER]: "Everything a Creator can, plus approve, schedule, publish",
};

export function TeamPage() {
  const user = useAuthStore((s) => s.user);
  const isOwner = user?.workspace?.is_own !== false; // default true while loading

  const queryClient = useQueryClient();
  const { data: members = [], isLoading } = useQuery({
    queryKey: ["team-members"],
    queryFn: () => teamsApi.listMembers(),
    enabled: isOwner,
  });
  const visibleMembers = members.filter((m) => m.status !== "revoked");

  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<TeamRole>(TeamRole.VIEWER);

  const inviteMutation = useMutation({
    mutationFn: () => teamsApi.invite({ email: inviteEmail.trim(), role: inviteRole }),
    onSuccess: () => {
      toast({ title: "Invitation sent", variant: "success" });
      setInviteEmail("");
      setInviteRole(TeamRole.VIEWER);
      setInviteOpen(false);
      queryClient.invalidateQueries({ queryKey: ["team-members"] });
    },
    onError: (err: unknown) => {
      toast({
        title: "Could not send invite",
        description: extractErrorMessage(err, "Something went wrong."),
        variant: "destructive",
      });
    },
  });

  const roleMutation = useMutation({
    mutationFn: ({ memberId, role }: { memberId: string; role: TeamRole }) =>
      teamsApi.changeRole(memberId, role),
    onSuccess: () => {
      toast({ title: "Role updated" });
      queryClient.invalidateQueries({ queryKey: ["team-members"] });
    },
    onError: (err: unknown) => {
      toast({
        title: "Could not update role",
        description: extractErrorMessage(err, "Something went wrong."),
        variant: "destructive",
      });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (memberId: string) => teamsApi.revoke(memberId),
    onSuccess: () => {
      toast({ title: "Access revoked" });
      queryClient.invalidateQueries({ queryKey: ["team-members"] });
    },
    onError: (err: unknown) => {
      toast({
        title: "Could not revoke access",
        description: extractErrorMessage(err, "Something went wrong."),
        variant: "destructive",
      });
    },
  });

  const handleRevoke = async (member: TeamMemberDto) => {
    const confirmed = await confirmAction({
      title: `Revoke ${member.email}?`,
      text: "They lose access to this workspace immediately.",
      confirmButtonText: "Revoke",
    });
    if (confirmed) revokeMutation.mutate(member.id);
  };

  if (!isOwner) {
    return (
      <div className="space-y-6" data-testid="team-page">
        <div>
          <h1 className="text-2xl font-bold text-text">Team</h1>
          <p className="text-sm text-text-dim">Manage who can access this workspace</p>
        </div>
        <div className="rounded-lg border border-border p-6 text-center text-sm text-text-muted">
          Only the workspace owner can manage team members.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="team-page">
      <div>
        <h1 className="text-2xl font-bold text-text">Team</h1>
        <p className="text-sm text-text-dim">
          Invite people into your workspace and control what they can do.
        </p>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">Team Members</CardTitle>
            <CardDescription>
              Viewers see everything; Creators can also build content; Publishers can also approve and publish it.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            data-testid="invite-team-button"
            onClick={() => setInviteOpen((v) => !v)}
          >
            {inviteOpen ? <X className="mr-2 h-4 w-4" /> : <Plus className="mr-2 h-4 w-4" />}
            {inviteOpen ? "Cancel" : "Invite"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {inviteOpen && (
            <div className="flex flex-col sm:flex-row gap-2 rounded-lg border border-border p-3">
              <Input
                placeholder="teammate@example.com"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                data-testid="team-email-input"
                className="flex-1"
              />
              <Select value={inviteRole} onValueChange={(v) => setInviteRole(v as TeamRole)}>
                <SelectTrigger className="sm:w-[160px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.values(TeamRole).map((r) => (
                    <SelectItem key={r} value={r}>
                      {ROLE_LABELS[r]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                onClick={() => inviteMutation.mutate()}
                disabled={!inviteEmail.trim() || inviteMutation.isPending}
                className="bg-accent hover:bg-accent/90"
              >
                {inviteMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Send invite"
                )}
              </Button>
            </div>
          )}

          {isLoading ? (
            <div className="flex items-center justify-center py-8 text-white/40 text-sm">
              <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading…
            </div>
          ) : visibleMembers.length === 0 ? (
            <div className="rounded-lg border border-border p-4 text-center text-sm text-text-muted">
              No team members yet
            </div>
          ) : (
            <ul className="space-y-2">
              {visibleMembers.map((m) => (
                <li
                  key={m.id}
                  className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-3"
                >
                  <div className="flex-1 min-w-[180px]">
                    <div className="text-sm font-medium text-text truncate">
                      {m.display_name || m.email}
                    </div>
                    {m.display_name && (
                      <div className="text-xs text-text-dim truncate">{m.email}</div>
                    )}
                  </div>

                  <span
                    className={cn(
                      "text-[10px] px-2 py-0.5 rounded-full font-medium",
                      m.status === "active" && "bg-green-500/15 text-green-400",
                      m.status === "pending" && "bg-amber-500/15 text-amber-300",
                      m.status === "revoked" && "bg-white/10 text-white/40",
                    )}
                  >
                    {m.status === "active" ? "Active" : m.status === "pending" ? "Pending" : "Revoked"}
                  </span>

                  <Select
                    value={m.role}
                    onValueChange={(v) => roleMutation.mutate({ memberId: m.id, role: v as TeamRole })}
                    disabled={m.status === "revoked"}
                  >
                    <SelectTrigger className="w-[150px] h-8 text-xs" title={ROLE_DESCRIPTIONS[m.role]}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.values(TeamRole).map((r) => (
                        <SelectItem key={r} value={r}>
                          {ROLE_LABELS[r]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {m.status !== "revoked" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleRevoke(m)}
                      className="text-red-400 hover:text-red-300"
                    >
                      Revoke
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
