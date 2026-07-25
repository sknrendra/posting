from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.dependencies import get_db, verify_csrf
from app.services import auth_service
from app.templating import templates

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(
        request, "auth/login.html", {"current_user": None}
    )


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: DbSession = Depends(get_db),
):
    user = auth_service.authenticate(db, email, password)
    if not user:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"current_user": None, "error": "Invalid email or password", "email": email},
            status_code=401,
        )
    token = auth_service.create_session(db, user)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=60 * 60 * 24 * settings.session_lifetime_days,
    )
    return response


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request, db: DbSession = Depends(get_db)):
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        auth_service.destroy_session(db, token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response
