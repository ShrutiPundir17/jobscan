import io

from docx import Document
from fastapi import HTTPException, status
from pypdf import PdfReader


def extract_text_from_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
        return "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read PDF: {exc}",
        ) from exc


def extract_text_from_docx(data: bytes) -> str:
    try:
        document = Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text and p.text.strip()]
        return "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read DOCX: {exc}",
        ) from exc


def extract_resume_text(*, filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_text_from_pdf(data)
    if lower.endswith(".docx"):
        return extract_text_from_docx(data)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported file type. Upload a PDF or DOCX file.",
    )
