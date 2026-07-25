from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.database import get_db
from app.exceptions import NotAuthenticatedError
from app.middleware import CSRF_COOKIE_NAME
from app.models.api_key import ApiKey
from app.models.user import User
from app.services import api_key_service, auth_service
from app.utils.security import constant_time_eq

__all__ = ["get_db", "require_login", "require_admin", "verify_csrf", "require_api_key"]


def require_login(request: Request, db: DbSession = Depends(get_db)) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    user = auth_service.get_user_for_session_token(db, token) if token else None
    if not user:
        raise NotAuthenticatedError()
    return user


def require_admin(current_user: User = Depends(require_login)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def require_api_key(request: Request, db: DbSession = Depends(get_db)) -> ApiKey:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header[7:].strip()
    api_key = api_key_service.verify_token(db, token)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    return api_key


async def verify_csrf(request: Request) -> None:
    form = await request.form()
    submitted = form.get("csrf_token")
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token or not submitted or not constant_time_eq(str(submitted), cookie_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
