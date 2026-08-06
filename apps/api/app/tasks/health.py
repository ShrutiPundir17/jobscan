from app.celery_app import celery


@celery.task(name="app.tasks.health.ping")
def ping() -> dict[str, str]:
    return {"status": "ok", "task": "health.ping"}
