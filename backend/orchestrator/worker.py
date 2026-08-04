"""Run with: python celery.py
Equivalent to: python -m celery -A tasks worker -l info --concurrency=2 --queues=default,renders --pool=threads

Listens on both queues so a single local worker handles everything —
production splits `renders` onto its own worker pool (see
tasks/cast_render.py's `queue="renders"`) since final video renders are
long-running and shouldn't starve quick default-queue jobs, but for local
dev there's no reason to run two worker processes.
"""
# Must be set before `from tasks import celery_app` — that import chain
# pulls in database.py, which reads this at module-import time to pick the
# engine's pool class. See database.py for why: --pool=threads runs each
# task's asyncio.run() on a fresh event loop, but SQLAlchemy's default
# pooled connections stay bound to whichever loop first used them, so a
# later task on a different thread/loop reusing a pooled connection blows
# up with "Future attached to a different loop" (pool_pre_ping's own ping
# is usually the first thing to hit it). NullPool avoids ever handing a
# connection across loops, at the cost of a fresh connection per task —
# fine for a worker that holds a connection briefly then moves on.
import os
os.environ.setdefault("ORCHESTRATOR_RUNTIME", "celery_worker")

from tasks import celery_app

if __name__ == "__main__":
    celery_app.worker_main([
        "worker",
        "-l", "info",
        "--concurrency=2",
        "--queues=default,renders",
        "--pool=threads",
    ])