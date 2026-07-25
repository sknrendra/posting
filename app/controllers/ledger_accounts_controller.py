from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_login, verify_csrf
from app.models.account import ACCOUNT_TYPES
from app.models.user import User
from app.services import account_service, ledger_service
from app.templating import templates

router = APIRouter(prefix="/accounts")
ledger_router = APIRouter(prefix="/ledger")


@router.get("")
def list_accounts(request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)):
    accounts = account_service.list_accounts(db)
    return templates.TemplateResponse(
        request, "accounts/list.html", {"current_user": current_user, "accounts": accounts}
    )


@router.get("/new")
def new_account_form(request: Request, current_user: User = Depends(require_login)):
    return templates.TemplateResponse(
        request,
        "accounts/form.html",
        {"current_user": current_user, "account": None, "account_types": ACCOUNT_TYPES},
    )


@router.post("/new", dependencies=[Depends(verify_csrf)])
def create_account(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    account_type: str = Form(...),
    is_cash_account: bool = Form(False),
    description: str = Form(""),
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    if account_type not in ACCOUNT_TYPES:
        raise HTTPException(status_code=422, detail="Invalid account type")
    account_service.create_account(
        db, code.strip(), name.strip(), account_type, is_cash_account, description.strip() or None
    )
    return RedirectResponse(url="/accounts?ok=Account created", status_code=303)


@router.get("/{account_id}/edit")
def edit_account_form(
    request: Request,
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "accounts/form.html",
        {"current_user": current_user, "account": account, "account_types": ACCOUNT_TYPES},
    )


@router.post("/{account_id}/edit", dependencies=[Depends(verify_csrf)])
def update_account(
    request: Request,
    account_id: int,
    name: str = Form(...),
    is_cash_account: bool = Form(False),
    description: str = Form(""),
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    account_service.update_account(db, account, name.strip(), description.strip() or None, is_cash_account)
    return RedirectResponse(url="/accounts?ok=Account updated", status_code=303)


@router.post("/{account_id}/deactivate", dependencies=[Depends(verify_csrf)])
def deactivate_account(
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    account_service.set_active(db, account, False)
    return RedirectResponse(url="/accounts?ok=Account deactivated", status_code=303)


@router.post("/{account_id}/activate", dependencies=[Depends(verify_csrf)])
def activate_account(
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    account_service.set_active(db, account, True)
    return RedirectResponse(url="/accounts?ok=Account activated", status_code=303)


@ledger_router.get("")
def ledger_picker(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    accounts = account_service.list_accounts(db, include_inactive=False)
    return templates.TemplateResponse(
        request, "ledger/picker.html", {"current_user": current_user, "accounts": accounts}
    )


@ledger_router.get("/{account_id}")
def ledger_detail(
    request: Request,
    account_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    parsed_from = date_cls.fromisoformat(date_from) if date_from else None
    parsed_to = date_cls.fromisoformat(date_to) if date_to else None
    result = ledger_service.get_running_ledger(db, account, date_from=parsed_from, date_to=parsed_to)
    return templates.TemplateResponse(
        request,
        "ledger/detail.html",
        {
            "current_user": current_user,
            "account": account,
            "date_from": date_from or "",
            "date_to": date_to or "",
            **result,
        },
    )
