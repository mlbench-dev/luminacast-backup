"""Admin guard — email allow-list for the cost dashboard and other
operator-only routes added in PR γ.

The project already has `routers.auth.require_admin` which checks
`User.role == UserRole.ADMIN`. That guard stays — it's the right primitive
for general admin access. This module adds a *separate* allow-list keyed
on email so the cost dashboard can be locked to a known operator
identity even if other ADMIN-roled accounts exist.
"""

from fastapi import Depends, HTTPException

from models.user import User
from routers.auth import get_current_user


# Operators who can see /admin/costs/*. Add an email here to grant access.
ADMIN_EMAILS = ["3gorka72@gmail.com"]


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
