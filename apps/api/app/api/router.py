from fastapi import APIRouter

from app.api import applications, jobs, matches, notifications, resumes, scanner, users
from app.api.auth import router as auth_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(users.router)
api_router.include_router(resumes.router)
api_router.include_router(jobs.router)
api_router.include_router(matches.router)
api_router.include_router(applications.router)
api_router.include_router(notifications.router)
api_router.include_router(scanner.router)
