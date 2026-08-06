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
        status["postgres"] = f"error: {exc}"
        http_status = 503

    try:
        client = Redis.from_url(settings.redis_url)
        if client.ping():
            status["redis"] = "ok"
        client.close()
    except Exception as exc:  # noqa: BLE001
        status["redis"] = f"error: {exc}"
        http_status = 503

    return JSONResponse(content=status, status_code=http_status)


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    return {"service": settings.app_name, "env": settings.app_env}
