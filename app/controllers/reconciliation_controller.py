from datetime import date as date_cls
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_admin, require_login, verify_csrf
from app.models.user import User
from app.services import account_service, reconciliation_service
from app.services.reconciliation_service import ReconciliationError
from app.templating import templates

router = APIRouter(prefix="/reconciliation")


def _parse_decimal(raw: str) -> Decimal:
    raw = (raw or "").strip()
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ReconciliationError(f"'{raw}' is not a valid amount")


def _parse_date(raw: str) -> date_cls:
    raw = (raw or "").strip()
    try:
        return date_cls.fromisoformat(raw)
    except ValueError:
        raise ReconciliationError(f"'{raw}' is not a valid date")


def _get_reconciliation_or_404(db: DbSession, reconciliation_id: int):
    reconciliation = reconciliation_service.get_reconciliation(db, reconciliation_id)
    if not reconciliation:
        raise HTTPException(status_code=404)
    return reconciliation


@router.get("")
def reconciliation_index(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    accounts = reconciliation_service.list_reconcilable_accounts(db)
    rows = [
        {
            "account": account,
            "active": reconciliation_service.get_active_reconciliation(db, account.id),
            "latest_completed": reconciliation_service.get_latest_completed(db, account.id),
        }
        for account in accounts
    ]
    return templates.TemplateResponse(
        request, "reconciliation/index.html", {"current_user": current_user, "rows": rows}
    )


@router.get("/{account_id}/new")
def new_reconciliation_form(
    request: Request,
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    active = reconciliation_service.get_active_reconciliation(db, account_id)
    if active:
        return RedirectResponse(url=f"/reconciliation/{active.id}", status_code=303)
    starting_balance = reconciliation_service.get_expected_starting_balance(db, account_id)
    warning = reconciliation_service.get_continuity_warning(db, account)
    return templates.TemplateResponse(
        request,
        "reconciliation/new.html",
        {
            "current_user": current_user,
            "account": account,
            "starting_balance": starting_balance,
            "warning": warning,
        },
    )


@router.post("/{account_id}/new", dependencies=[Depends(verify_csrf)])
async def start_reconciliation(
    request: Request,
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    form = await request.form()
    try:
        statement_date = _parse_date(str(form.get("statement_date", "")))
        statement_ending_balance = _parse_decimal(str(form.get("statement_ending_balance", "")))
        reconciliation, warning = reconciliation_service.start_reconciliation(
            db, account_id, statement_date, statement_ending_balance, current_user
        )
    except ReconciliationError as exc:
        starting_balance = reconciliation_service.get_expected_starting_balance(db, account_id)
        return templates.TemplateResponse(
            request,
            "reconciliation/new.html",
            {
                "current_user": current_user,
                "account": account,
                "starting_balance": starting_balance,
                "warning": None,
                "error": str(exc),
            },
            status_code=422,
        )
    url = f"/reconciliation/{reconciliation.id}"
    if warning:
        url += f"?warning={warning}"
    return RedirectResponse(url=url, status_code=303)


@router.get("/{reconciliation_id}")
def reconciliation_detail(
    request: Request,
    reconciliation_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    reconciliation = _get_reconciliation_or_404(db, reconciliation_id)
    diff = reconciliation_service.compute_difference(db, reconciliation)
    if reconciliation.status == "in_progress":
        accounts = [
            a for a in account_service.list_accounts(db, include_inactive=False)
            if a.id != reconciliation.account_id
        ]
        lines = reconciliation_service.get_clearable_lines(db, reconciliation)
        return templates.TemplateResponse(
            request,
            "reconciliation/work.html",
            {
                "current_user": current_user,
                "reconciliation": reconciliation,
                "diff": diff,
                "lines": lines,
                "counter_accounts": accounts,
                "today": date_cls.today().isoformat(),
            },
        )
    return templates.TemplateResponse(
        request,
        "reconciliation/summary.html",
        {"current_user": current_user, "reconciliation": reconciliation, "diff": diff},
    )


@router.post(
    "/{reconciliation_id}/lines/{line_id}/toggle-cleared", dependencies=[Depends(verify_csrf)]
)
async def toggle_line_cleared(
    request: Request,
    reconciliation_id: int,
    line_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    reconciliation = _get_reconciliation_or_404(db, reconciliation_id)
    form = await request.form()
    cleared = str(form.get("cleared", "")).strip().lower() == "true"
    try:
        reconciliation_service.toggle_line_cleared(db, reconciliation, line_id, cleared)
    except ReconciliationError as exc:
        return RedirectResponse(
            url=f"/reconciliation/{reconciliation_id}?error={exc}", status_code=303
        )
    return RedirectResponse(url=f"/reconciliation/{reconciliation_id}", status_code=303)


@router.post("/{reconciliation_id}/add-transaction", dependencies=[Depends(verify_csrf)])
async def add_missing_transaction(
    request: Request,
    reconciliation_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    reconciliation = _get_reconciliation_or_404(db, reconciliation_id)
    form = await request.form()
    try:
        entry_date = _parse_date(str(form.get("entry_date", "")))
        amount = _parse_decimal(str(form.get("amount", "")))
        movement = str(form.get("movement", ""))
        counter_account_id = int(form.get("counter_account_id", "0"))
        memo = str(form.get("memo", "")).strip()
        if not memo:
            raise ReconciliationError("Memo is required")
        reconciliation_service.add_missing_transaction(
            db,
            reconciliation,
            counter_account_id=counter_account_id,
            amount=amount,
            movement=movement,
            entry_date=entry_date,
            memo=memo,
            user=current_user,
        )
    except (ReconciliationError, ValueError) as exc:
        return RedirectResponse(
            url=f"/reconciliation/{reconciliation_id}?error={exc}", status_code=303
        )
    return RedirectResponse(
        url=f"/reconciliation/{reconciliation_id}?ok=Transaction added", status_code=303
    )


@router.post("/{reconciliation_id}/complete", dependencies=[Depends(verify_csrf)])
def complete_reconciliation(
    reconciliation_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    reconciliation = _get_reconciliation_or_404(db, reconciliation_id)
    try:
        reconciliation_service.complete_reconciliation(db, reconciliation, current_user)
    except ReconciliationError as exc:
        return RedirectResponse(
            url=f"/reconciliation/{reconciliation_id}?error={exc}", status_code=303
        )
    return RedirectResponse(
        url=f"/reconciliation/{reconciliation_id}?ok=Reconciliation completed", status_code=303
    )


@router.get("/{reconciliation_id}/undo")
def undo_reconciliation_form(
    request: Request,
    reconciliation_id: int,
    current_user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    reconciliation = _get_reconciliation_or_404(db, reconciliation_id)
    later = (
        reconciliation_service.get_history(db, reconciliation.account_id)
        if reconciliation.status == "completed"
        else []
    )
    later = [
        r for r in later if r.status == "completed" and r.statement_date > reconciliation.statement_date
    ]
    return templates.TemplateResponse(
        request,
        "reconciliation/undo_confirm.html",
        {"current_user": current_user, "reconciliation": reconciliation, "later": later},
    )


@router.post("/{reconciliation_id}/undo", dependencies=[Depends(verify_csrf)])
def undo_reconciliation(
    reconciliation_id: int,
    current_user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    reconciliation = _get_reconciliation_or_404(db, reconciliation_id)
    try:
        reconciliation_service.undo_reconciliation(db, reconciliation, current_user)
    except ReconciliationError as exc:
        return RedirectResponse(
            url=f"/reconciliation/{reconciliation_id}?error={exc}", status_code=303
        )
    return RedirectResponse(
        url=f"/reconciliation/{reconciliation.account_id}/history?ok=Reconciliation undone",
        status_code=303,
    )


@router.get("/{account_id}/history")
def reconciliation_history(
    request: Request,
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    account = account_service.get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404)
    history = reconciliation_service.get_history(db, account_id)
    return templates.TemplateResponse(
        request,
        "reconciliation/history.html",
        {"current_user": current_user, "account": account, "history": history},
    )
