from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse


def not_implemented(feature: str, **extra: Any) -> JSONResponse:
    """Standard stub response for unfinished endpoints."""
    payload: dict[str, Any] = {
        "detail": "Not implemented yet",
        "feature": feature,
    }
    if extra:
        payload.update(extra)
    return JSONResponse(status_code=status.HTTP_501_NOT_IMPLEMENTED, content=payload)
