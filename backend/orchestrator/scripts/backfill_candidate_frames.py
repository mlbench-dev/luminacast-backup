"""One-time backfill: strip CDN domain from candidate_frames.

Converts full URLs (https://media.luminacast.com/creators/...) to R2 keys
(creators/...) so the serialization layer can append the CDN domain at
response time (B-068).

Usage:
    cd backend/orchestrator
    python scripts/backfill_candidate_frames.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from config import settings
from models.avatar import Avatar

CDN_PREFIX = "https://media.luminacast.com/"


def main():
    engine = create_engine(settings.sync_database_url)
    with Session(engine) as session:
        avatars = session.execute(
            select(Avatar).where(Avatar.candidate_frames.isnot(None))
        ).scalars().all()

        updated = 0
        for avatar in avatars:
            frames = avatar.candidate_frames
            if not frames:
                continue

            cleaned = []
            changed = False
            for frame in frames:
                if isinstance(frame, str) and frame.startswith(CDN_PREFIX):
                    cleaned.append(frame.replace(CDN_PREFIX, "", 1))
                    changed = True
                else:
                    cleaned.append(frame)

            if changed:
                avatar.candidate_frames = cleaned
                updated += 1
                print(f"  Updated {avatar.id}: {len(cleaned)} frames")

        session.commit()
        print(f"\nDone. Updated {updated} avatars out of {len(avatars)} with candidate_frames.")


if __name__ == "__main__":
    main()
