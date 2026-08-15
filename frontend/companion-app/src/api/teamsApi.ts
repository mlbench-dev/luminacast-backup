// ── Teams ──

import type {
  TeamRole,
  TokenResponse,
} from "@/lib/types";
import { api } from "@/lib/apiClient";

export interface TeamMemberDto {
  id: string;
  email: string;
  display_name?: string | null;
  role: TeamRole;
  status: "pending" | "active" | "revoked";
  invited_at: string;
  accepted_at?: string | null;
}

export interface WorkspaceOptionDto {
  owner_id: string;
  owner_label: string;
  role?: TeamRole | null;
  is_own: boolean;
}

export const teamsApi = {
  listMembers: () =>
    api.get<{ members: TeamMemberDto[] }>("/teams/members").then((r) => r.data.members),
  invite: (data: { email: string; role: TeamRole }) =>
    api.post<TeamMemberDto>("/teams/invite", data).then((r) => r.data),
  changeRole: (memberId: string, role: TeamRole) =>
    api.patch<TeamMemberDto>(`/teams/members/${memberId}/role`, { role }).then((r) => r.data),
  revoke: (memberId: string) =>
    api.delete(`/teams/members/${memberId}`).then((r) => r.data),
  previewInvite: (token: string) =>
    api
      .get<{ email: string; owner_label: string; role: TeamRole; requires_password: boolean }>(
        "/teams/accept-invite/preview",
        { params: { token } },
      )
      .then((r) => r.data),
  acceptInvite: (data: { token: string; password?: string }) =>
    api.post<TokenResponse>("/teams/accept-invite", data).then((r) => r.data),
  myWorkspaces: () =>
    api.get<{ workspaces: WorkspaceOptionDto[] }>("/teams/my-workspaces").then((r) => r.data.workspaces),
  switchWorkspace: (ownerId: string) =>
    api.post<TokenResponse>("/teams/switch-workspace", { owner_id: ownerId }).then((r) => r.data),
};
