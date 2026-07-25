from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_admin, require_login, verify_csrf
from app.models.user import User
from app.services import api_key_service, auth_service, user_service
from app.templating import templates
from app.utils.security import verify_password

router = APIRouter(prefix="/settings")

NEW_API_KEY_COOKIE = "flash_new_api_key"


@router.get("/api-keys")
def list_api_keys(
    request: Request, current_user: User = Depends(require_admin), db: DbSession = Depends(get_db)
):
    api_keys = api_key_service.list_api_keys(db)
    new_token = request.cookies.get(NEW_API_KEY_COOKIE)
    response = templates.TemplateResponse(
        request,
        "settings/api_keys.html",
        {"current_user": current_user, "api_keys": api_keys, "new_token": new_token},
    )
    if new_token:
        response.delete_cookie(NEW_API_KEY_COOKIE)
    return response


@router.post("/api-keys", dependencies=[Depends(verify_csrf)])
def create_api_key(
    name: str = Form(...),
    current_user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    _api_key, token = api_key_service.create_api_key(db, name.strip(), current_user.id)
    response = RedirectResponse(url="/settings/api-keys", status_code=303)
    # Full token is shown exactly once; carried via a short-lived httponly cookie (not the
    # URL, so it never lands in browser history / server access logs / the Referer header).
    response.set_cookie(NEW_API_KEY_COOKIE, token, httponly=True, samesite="lax", max_age=60)
    return response


@router.post("/api-keys/{api_key_id}/revoke", dependencies=[Depends(verify_csrf)])
def revoke_api_key(
    api_key_id: int,
    current_user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    api_key = api_key_service.get_api_key(db, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404)
    api_key_service.revoke(db, api_key)
    return RedirectResponse(url="/settings/api-keys?ok=API key revoked", status_code=303)


@router.get("/users")
def list_users(
    request: Request, current_user: User = Depends(require_admin), db: DbSession = Depends(get_db)
):
    users = user_service.list_users(db)
    return templates.TemplateResponse(
        request, "settings/users.html", {"current_user": current_user, "users": users}
    )


@router.post("/users", dependencies=[Depends(verify_csrf)])
def create_user(
    email: str = Form(...),
    password: str = Form(...),
    is_admin: bool = Form(False),
    current_user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < 8:
        return RedirectResponse(
            url="/settings/users?error=Password must be at least 8 characters", status_code=303
        )
    if user_service.get_user_by_email(db, email):
        return RedirectResponse(url="/settings/users?error=Email already in use", status_code=303)
    auth_service.create_user(db, email, password, is_admin=is_admin)
    return RedirectResponse(url="/settings/users?ok=User created", status_code=303)


@router.post("/users/{user_id}/deactivate", dependencies=[Depends(verify_csrf)])
def deactivate_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    if user_id == current_user.id:
        return RedirectResponse(
            url="/settings/users?error=You cannot deactivate your own account", status_code=303
        )
    user = user_service.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404)
    user_service.set_active(db, user, False)
    return RedirectResponse(url="/settings/users?ok=User deactivated", status_code=303)


@router.post("/users/{user_id}/activate", dependencies=[Depends(verify_csrf)])
def activate_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    user = user_service.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404)
    user_service.set_active(db, user, True)
    return RedirectResponse(url="/settings/users?ok=User activated", status_code=303)


@router.get("/password")
def password_form(request: Request, current_user: User = Depends(require_login)):
    return templates.TemplateResponse(
        request, "settings/password.html", {"current_user": current_user}
    )


@router.post("/password", dependencies=[Depends(verify_csrf)])
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    if not verify_password(current_password, current_user.password_hash):
        return templates.TemplateResponse(
            request,
            "settings/password.html",
            {"current_user": current_user, "error": "Current password is incorrect"},
            status_code=401,
        )
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request,
            "settings/password.html",
            {"current_user": current_user, "error": "New passwords do not match"},
            status_code=422,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request,
            "settings/password.html",
            {"current_user": current_user, "error": "New password must be at least 8 characters"},
            status_code=422,
        )
    auth_service.set_password(db, current_user, new_password)
    return RedirectResponse(url="/settings/password?ok=Password updated", status_code=303)
