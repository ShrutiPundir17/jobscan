from pathlib import Path
from uuid import UUID, uuid4

from app.config import settings


def ensure_upload_dir() -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_resume_storage_path(*, user_id: UUID, filename: str) -> Path:
    safe_name = Path(filename).name.replace(" ", "_")
    user_dir = ensure_upload_dir() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / f"{uuid4().hex}_{safe_name}"


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
