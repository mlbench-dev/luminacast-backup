import { useState } from "react";
import { Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/hooks/useToast";

export function TeamPage() {
  const [teamEmail, setTeamEmail] = useState("");

  return (
    <div className="space-y-6" data-testid="team-page">
      <div>
        <h1 className="text-2xl font-bold text-text">Team</h1>
        <p className="text-sm text-text-dim">Manage team members who can help during live streams</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Team Members</CardTitle>
          <CardDescription>Invite a team member to help manage chat during live streams</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input
              placeholder="team@example.com"
              value={teamEmail}
              onChange={(e) => setTeamEmail(e.target.value)}
              data-testid="team-email-input"
            />
            <Button
              variant="outline"
              data-testid="invite-team-button"
              onClick={() => {
                toast({ title: "Invitation sent", variant: "success" });
                setTeamEmail("");
              }}
            >
              <Users className="mr-2 h-4 w-4" />
              Invite
            </Button>
          </div>
          <div className="rounded-lg border border-border p-4 text-center text-sm text-text-muted">
            No team members yet
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
