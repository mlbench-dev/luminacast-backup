#!/usr/bin/env python3
"""Create or reset the admin user for fresh deployments.

Usage (inside the orchestrator container):
    python scripts/create_admin.py

Reads from environment variables (or .env):
    ADMIN_EMAIL    — admin login email  (required)
    ADMIN_PASSWORD — admin password      (required)
"""
import asyncio
import os
import sys
import uuid

# Ensure the orchestrator package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from passlib.context import CryptContext

from config import settings
from database import async_session_factory, engine, Base
from models.user import User, UserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def create_admin():
    admin_email = settings.ADMIN_EMAIL or os.environ.get("ADMIN_EMAIL", "")
    admin_password = settings.ADMIN_PASSWORD or os.environ.get("ADMIN_PASSWORD", "")

    if not admin_email:
        print("ERROR: ADMIN_EMAIL environment variable is required.")
        sys.exit(1)
    if not admin_password:
        print("ERROR: ADMIN_PASSWORD environment variable is required.")
        sys.exit(1)

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == admin_email)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update password in case it changed
            existing.password_hash = pwd_context.hash(admin_password)
            existing.role = UserRole.ADMIN
            existing.is_active = True
            await session.commit()
            print(f"Admin user updated: {admin_email}")
        else:
            user = User(
                id=f"usr_admin_{uuid.uuid4().hex[:8]}",
                email=admin_email,
                password_hash=pwd_context.hash(admin_password),
                role=UserRole.ADMIN,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            print(f"Admin user created: {admin_email}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(create_admin())
