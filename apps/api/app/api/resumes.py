from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.db import get_db
from app.models import Resume, User
from app.schemas.resumes import (
    EmbeddingStatusResponse,
    ResumeDeleteResponse,
    ResumeListResponse,
    ResumeResponse,
)
from app.services import resume_admin
from app.services.embeddings import embed_resume
from app.services.resume_parser import parse_resume_text
from app.services.resume_text import extract_resume_text
from app.services.storage import build_resume_storage_path, save_bytes

router = APIRouter(prefix="/resumes", tags=["resumes"])

ALLOWED_EXTENSIONS = {".pdf", ".docx"}


def _get_user_resume(db: Session, *, user_id: UUID, resume_id: UUID) -> Resume:
    resume = db.scalar(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    return resume


@router.post("/upload", response_model=ResumeResponse, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Resume:
    """Upload a resume file (PDF/DOCX), extract text, and store it."""
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Upload a PDF or DOCX file.",
        )

    # Sync endpoint: PDF parsing and DB locking must not block the event loop.
    # Read at most one byte past the limit instead of buffering any size.
    data = file.file.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max size is {settings.max_upload_bytes} bytes.",
        )

    raw_text = extract_resume_text(filename=file.filename, data=data)
    storage_path = build_resume_storage_path(user_id=current_user.id, filename=file.filename)
    save_bytes(storage_path, data)

    try:
        # Two uploads at once must not both become (or both skip being) primary.
        resume_admin.lock_user(db, current_user.id)
        existing_count = db.scalar(
            select(func.count()).select_from(Resume).where(Resume.user_id == current_user.id)
        ) or 0
        resume = Resume(
            user_id=current_user.id,
            filename=file.filename,
            storage_path=str(storage_path),
            file_data=data,
            raw_text=raw_text or None,
            parsed_data=None,
            is_primary=existing_count == 0,
        )
        db.add(resume)
        db.commit()
    except Exception:
        db.rollback()
        resume_admin.remove_stored_file(str(storage_path))  # no orphan files on failure
        raise
    db.refresh(resume)
    return resume


@router.get("", response_model=ResumeListResponse)
def list_resumes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeListResponse:
    """List all resumes for the current user."""
    resumes = db.scalars(
        select(Resume)
        .where(Resume.user_id == current_user.id)
        .order_by(Resume.created_at.desc())
    ).all()
    return ResumeListResponse(items=list(resumes), total=len(resumes))


@router.get("/{resume_id}", response_model=ResumeResponse)
def get_resume(
    resume_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Resume:
    """Get a single resume (raw text + parsed data)."""
    return _get_user_resume(db, user_id=current_user.id, resume_id=resume_id)


@router.delete("/{resume_id}", response_model=ResumeDeleteResponse)
def delete_resume(
    resume_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeDeleteResponse:
    """
    Delete a resume and its stored file.

    If it was primary, the best remaining resume (embedded first, then newest)
    becomes primary. Auto-apply plans waiting to use it are retired; 409 while
    a submission using it is in flight.
    """
    new_primary_id, retired = resume_admin.delete_resume(db, current_user, resume_id)
    return ResumeDeleteResponse(
        id=resume_id,
        new_primary_id=new_primary_id,
        retired_auto_apply_plans=retired,
    )


@router.post("/{resume_id}/set-primary", response_model=ResumeResponse)
def set_primary_resume(
    resume_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Resume:
    """
    Make this resume the one used for matching and auto-apply.

    Idempotent. 409 if it has no embedding yet (parse it first).
    """
    return resume_admin.set_primary(db, current_user, resume_id)


@router.post("/{resume_id}/parse", response_model=ResumeResponse)
def parse_resume(
    resume_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Resume:
    """Parse resume text with Gemini into structured JSON, then generate embedding."""
    resume = _get_user_resume(db, user_id=current_user.id, resume_id=resume_id)
    resume.parsed_data = parse_resume_text(resume.raw_text or "")
    db.add(resume)
    db.commit()
    db.refresh(resume)
    # Embedding powers future job matching
    return embed_resume(db, resume)


@router.post("/{resume_id}/embed", response_model=ResumeResponse)
def generate_resume_embedding(
    resume_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Resume:
    """Generate / refresh vector embedding for a resume."""
    resume = _get_user_resume(db, user_id=current_user.id, resume_id=resume_id)
    return embed_resume(db, resume)


@router.get("/{resume_id}/embedding", response_model=EmbeddingStatusResponse)
def get_resume_embedding_status(
    resume_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmbeddingStatusResponse:
    """Check whether a resume has an embedding (does not return the raw vector)."""
    resume = _get_user_resume(db, user_id=current_user.id, resume_id=resume_id)
    return EmbeddingStatusResponse(
        resume_id=resume.id,
        has_embedding=resume.embedding is not None,
        embedding_model=resume.embedding_model,
        embedded_at=resume.embedded_at,
        dimensions=len(resume.embedding) if resume.embedding is not None else 0,
    )
