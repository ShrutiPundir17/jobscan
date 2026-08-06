from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas.users import UserPreferencesResponse, UserPreferencesUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPreferencesResponse)
def get_preferences(current_user: User = Depends(get_current_user)) -> User:
    """Get current user profile and job-search preferences."""
    return current_user


@router.patch("/me", response_model=UserPreferencesResponse)
def update_preferences(
    payload: UserPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Update target roles, locations, auto-apply, and related preferences."""
    data = payload.model_dump(exclude_unset=True)

    if "full_name" in data:
        name = data["full_name"]
        current_user.full_name = name.strip() if isinstance(name, str) and name.strip() else None
    if "auto_apply_enabled" in data:
        current_user.auto_apply_enabled = bool(data["auto_apply_enabled"])
    if "min_match_score" in data and data["min_match_score"] is not None:
        current_user.min_match_score = int(data["min_match_score"])
    if "target_roles" in data and data["target_roles"] is not None:
        current_user.target_roles = data["target_roles"]
    if "preferred_locations" in data and data["preferred_locations"] is not None:
        current_user.preferred_locations = data["preferred_locations"]
    if "phone" in data:
        phone = data["phone"]
        current_user.phone = phone.strip() if isinstance(phone, str) and phone.strip() else None
    if "notify_email_enabled" in data and data["notify_email_enabled"] is not None:
        current_user.notify_email_enabled = bool(data["notify_email_enabled"])
    if "notify_whatsapp_enabled" in data and data["notify_whatsapp_enabled"] is not None:
        current_user.notify_whatsapp_enabled = bool(data["notify_whatsapp_enabled"])

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user
