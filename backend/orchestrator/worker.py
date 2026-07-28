"""Run with: python celery.py
Equivalent to: python -m celery -A tasks worker -l info --concurrency=2 --queues=default,renders --pool=threads

Listens on both queues so a single local worker handles everything —
production splits `renders` onto its own worker pool (see
tasks/cast_render.py's `queue="renders"`) since final video renders are
long-running and shouldn't starve quick default-queue jobs, but for local
dev there's no reason to run two worker processes.
"""
from tasks import celery_app

if __name__ == "__main__":
    celery_app.worker_main([
        "worker",
        "-l", "info",
        "--concurrency=2",
        "--queues=default,renders",
        "--pool=threads",
    ])