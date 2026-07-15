"""Unit test guarding the Celery `include=[...]` list in `tasks/__init__.py`.

Regression coverage for the live bug where Mubert background music was
never generated because the worker rejected the message with
`Received unregistered task of type 'tasks.auto_music.generate_for_cast'`
→ `KeyError: 'tasks.auto_music.generate_for_cast'`.

`routers/casts.py` dispatches `generate_for_cast` via `.delay()`, but the
worker only registers task modules listed in `celery_app`'s `include`.
If `tasks.auto_music` is dropped from that list, the task disappears from
`celery_app.tasks` and the dispatch silently fails at the broker again.
"""
from tasks import celery_app


def test_auto_music_registered():
    # Modules in `include` are imported lazily when the worker boots; force
    # that import here so the task table reflects what a real worker registers.
    celery_app.loader.import_default_modules()
    assert "tasks.auto_music.generate_for_cast" in celery_app.tasks
