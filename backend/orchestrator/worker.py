"""Run with: python celery.py
Equivalent to: python -m celery -A tasks worker -l info --concurrency=2 --queues=default --pool=threads
"""
from tasks import celery_app

if __name__ == "__main__":
    celery_app.worker_main([
        "worker",
        "-l", "info",
        "--concurrency=2",
        "--queues=default",
        "--pool=threads",
    ])