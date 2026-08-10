from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from models.user import TeamRole


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in (r.value for r in TeamRole):
            raise ValueError(f"role must be one of: {', '.join(r.value for r in TeamRole)}")
        return v


class ChangeRoleRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in (r.value for r in TeamRole):
            raise ValueError(f"role must be one of: {', '.join(r.value for r in TeamRole)}")
        return v


class TeamMemberResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    role: str
    status: str
    invited_at: datetime
    accepted_at: Optional[datetime] = None


class TeamMembersListResponse(BaseModel):
    members: list[TeamMemberResponse]


class AcceptInvitePreviewResponse(BaseModel):
    email: str
    owner_label: str
    role: str
    requires_password: bool


class AcceptInviteRequest(BaseModel):
    token: str
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class WorkspaceOption(BaseModel):
    owner_id: str
    owner_label: str
    role: Optional[str] = None  # null when is_own is True
    is_own: bool


class MyWorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceOption]


class SwitchWorkspaceRequest(BaseModel):
    owner_id: str
