from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "jobagent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.health",
        "app.tasks.job_scanner",
        "app.tasks.job_embeddings",
        "app.tasks.notify_matches",
    ],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Job portal crawl — every 2 hours at :00 UTC
        "scan-jobs-every-2-hours": {
            "task": "app.tasks.job_scanner.scan_jobs",
            "schedule": crontab(minute=0, hour="*/2"),
        },
        # Backfill / catch-up job embeddings for Stage 1 matching
        "embed-pending-jobs-every-30-min": {
            "task": "app.tasks.job_embeddings.embed_pending_jobs",
            "schedule": crontab(minute="*/30"),
        },
        "health-ping-every-hour": {
            "task": "app.tasks.health.ping",
            "schedule": crontab(minute=0),
        },
    },
)
