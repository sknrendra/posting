from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.controllers import (
    auth_controller,
    balance_controller,
    journal_entries_controller,
    ledger_accounts_controller,
    reconciliation_controller,
    reports_controller,
    settings_controller,
    webhook_controller,
)
from app.dependencies import require_login
from app.exceptions import NotAuthenticatedError
from app.middleware import CsrfCookieMiddleware
from app.models.user import User
from app.templating import templates

app = FastAPI(title="Posting")

app.add_middleware(CsrfCookieMiddleware)


@app.exception_handler(NotAuthenticatedError)
def not_authenticated_handler(request: Request, exc: NotAuthenticatedError):
    return RedirectResponse(url="/login", status_code=303)


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth_controller.router)
app.include_router(ledger_accounts_controller.router)
app.include_router(ledger_accounts_controller.ledger_router)
app.include_router(journal_entries_controller.router)
app.include_router(balance_controller.router)
app.include_router(reconciliation_controller.router)
app.include_router(reports_controller.router)
app.include_router(settings_controller.router)
app.include_router(webhook_controller.router)


@app.get("/")
def home(request: Request, current_user: User = Depends(require_login)):
    return templates.TemplateResponse(request, "home.html", {"current_user": current_user})


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("app/static/favicon/favicon.ico")
