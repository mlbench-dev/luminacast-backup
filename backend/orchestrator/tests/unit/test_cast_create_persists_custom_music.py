"""create_cast must persist a picked "custom" background track.

Bug: the SetupPhase music picker sends background_music_url / _mood at
create time, but create_cast only copied ``music_track_choice`` — the url
was dropped, so on the next reload the picker showed "no track selected"
(and the render had no music). PUT/PATCH already persisted it; create did not.

DB-free source guard (the unit CI job has no Postgres): the schema already
accepts the fields (asserted below); this pins that the create handler now
copies them onto the Cast row and re-hosts an external URL.
"""
from pathlib import Path

from schemas.cast import CastCreate

_CRUD = Path(__file__).resolve().parents[2] / "routers" / "casts" / "crud.py"


def test_cast_create_schema_accepts_background_music_fields():
    req = CastCreate(
        avatar_id="avt_1",
        music_track_choice="custom",
        background_music_url="https://music-cdn.example/track.mp3",
        background_music_mood="chill",
        background_music_tags=["lofi", "calm"],
    )
    assert req.background_music_url == "https://music-cdn.example/track.mp3"
    assert req.background_music_mood == "chill"
    assert req.background_music_tags == ["lofi", "calm"]


def test_create_cast_handler_copies_and_rehosts_background_music():
    src = _CRUD.read_text(encoding="utf-8")
    start = src.index("async def create_cast(")
    end = src.index("\n@router", start)
    body = src[start:end]

    # url is re-hosted before it's stored
    assert "rehost_external_music_url(" in body
    # …and all three picked-track fields land on the Cast row
    assert "background_music_url=bg_music_url" in body
    assert "background_music_mood=req.background_music_mood" in body
    assert "background_music_tags=req.background_music_tags" in body
