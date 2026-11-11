from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis import Redis

from app.api.router import api_router
from app.config import settings
from app.db import check_database
from app import models  # noqa: F401 — ensure models are registered

app = FastAPI(title=settings.app_name, debug=settings.debug)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/health", tags=["system"])
def health() -> JSONResponse:
    status = {"app": "ok", "postgres": "down", "redis": "down"}
    http_status = 200

    try:
        check_database()
        status["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 — surface infra errors in health
        status["postgres"] = f"error: {type(exc).__name__}"
        http_status = 503

    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
        if client.ping():
            status["redis"] = "ok"
        client.close()
    except Exception as exc:  # noqa: BLE001
        status["redis"] = f"error: {type(exc).__name__}"
        # Without a Celery worker Redis only holds scan bookkeeping (and every
        # guard fails open), so a Redis hiccup must not take the API offline.
        if settings.task_mode != "inline":
            http_status = 503

    return JSONResponse(content=status, status_code=http_status)


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    return {"service": settings.app_name, "env": settings.app_env}
