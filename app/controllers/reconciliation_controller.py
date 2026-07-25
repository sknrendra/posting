from datetime import date as date_cls
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_login, verify_csrf
from app.models.user import User
from app.services import reconciliation_service
from app.services.reconciliation_service import ReconciliationError
from app.templating import templates
from app.utils import periods as period_utils

router = APIRouter(prefix="/reconciliation")


@router.get("")
def reconciliation_index(
    request: Request,
    period_type: str = "monthly",
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    if period_type not in period_utils.PERIOD_TYPES:
        raise HTTPException(status_code=404)
    periods = reconciliation_service.list_periods(db, period_type)
    period_rows = [
        {
            "period_start": start,
            "period_end": end,
            "status": reconciliation_service.get_period_status(db, period_type, start, end),
        }
        for start, end in periods
    ]
    return templates.TemplateResponse(
        request,
        "reconciliation/index.html",
        {
            "current_user": current_user,
            "period_type": period_type,
            "period_types": period_utils.PERIOD_TYPES,
            "period_rows": period_rows,
        },
    )


@router.get("/{period_type}/{period_start}")
def reconciliation_detail(
    request: Request,
    period_type: str,
    period_start: str,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    if period_type not in period_utils.PERIOD_TYPES:
        raise HTTPException(status_code=404)
    start_date = date_cls.fromisoformat(period_start)
    start, end = period_utils.period_bounds(period_type, start_date)
    rows = reconciliation_service.get_reconciliation_rows(db, period_type, start, end)
    status = reconciliation_service.get_period_status(db, period_type, start, end)
    return templates.TemplateResponse(
        request,
        "reconciliation/detail.html",
        {
            "current_user": current_user,
            "period_type": period_type,
            "period_start": start,
            "period_end": end,
            "rows": rows,
            "status": status,
        },
    )


@router.post("/{period_type}/{period_start}/{account_id}", dependencies=[Depends(verify_csrf)])
async def submit_statement_balance(
    request: Request,
    period_type: str,
    period_start: str,
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    if period_type not in period_utils.PERIOD_TYPES:
        raise HTTPException(status_code=404)
    start_date = date_cls.fromisoformat(period_start)
    start, end = period_utils.period_bounds(period_type, start_date)
    form = await request.form()
    try:
        statement_balance = Decimal(str(form.get("statement_balance", "")).strip())
    except InvalidOperation:
        return RedirectResponse(
            url=f"/reconciliation/{period_type}/{period_start}?error=Invalid statement balance",
            status_code=303,
        )
    try:
        reconciliation_service.submit_statement_balance(
            db, period_type, start, end, account_id, statement_balance, current_user
        )
    except ReconciliationError as exc:
        return RedirectResponse(
            url=f"/reconciliation/{period_type}/{period_start}?error={exc}", status_code=303
        )
    return RedirectResponse(url=f"/reconciliation/{period_type}/{period_start}", status_code=303)


@router.post(
    "/{period_type}/{period_start}/{account_id}/confirm", dependencies=[Depends(verify_csrf)]
)
def confirm_reconciliation(
    period_type: str,
    period_start: str,
    account_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    if period_type not in period_utils.PERIOD_TYPES:
        raise HTTPException(status_code=404)
    start_date = date_cls.fromisoformat(period_start)
    start, end = period_utils.period_bounds(period_type, start_date)
    try:
        reconciliation_service.confirm_reconciliation(
            db, period_type, start, end, account_id, current_user
        )
    except ReconciliationError as exc:
        return RedirectResponse(
            url=f"/reconciliation/{period_type}/{period_start}?error={exc}", status_code=303
        )
    return RedirectResponse(
        url=f"/reconciliation/{period_type}/{period_start}?ok=Reconciled", status_code=303
    )
