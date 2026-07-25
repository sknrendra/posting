from datetime import date as date_cls
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_login, verify_csrf
from app.models.user import User
from app.services import account_service, journal_service
from app.services.journal_service import InvalidJournalEntryError, LineInput
from app.templating import templates

router = APIRouter(prefix="/journal-entries")


def _parse_decimal(raw: str) -> Decimal:
    raw = (raw or "").strip()
    if not raw:
        return Decimal("0")
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise InvalidJournalEntryError(f"'{raw}' is not a valid amount")


@router.get("")
def list_journal_entries(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    entries = journal_service.list_entries(db)
    return templates.TemplateResponse(
        request, "journal_entries/list.html", {"current_user": current_user, "entries": entries}
    )


@router.get("/new")
def new_journal_entry_form(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    accounts = account_service.list_accounts(db, include_inactive=False)
    return templates.TemplateResponse(
        request,
        "journal_entries/form.html",
        {
            "current_user": current_user,
            "accounts": accounts,
            "entry_date": date_cls.today().isoformat(),
            "memo": "",
            "rows": [{"account_id": "", "debit_amount": "", "credit_amount": "", "memo": ""}] * 2,
        },
    )


@router.post("/new", dependencies=[Depends(verify_csrf)])
async def create_journal_entry(
    request: Request,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    form = await request.form()
    entry_date_raw = str(form.get("entry_date", ""))
    memo = str(form.get("memo", "")).strip()
    account_ids = form.getlist("account_id")
    debit_amounts = form.getlist("debit_amount")
    credit_amounts = form.getlist("credit_amount")
    line_memos = form.getlist("line_memo")

    rows = [
        {
            "account_id": account_ids[i] if i < len(account_ids) else "",
            "debit_amount": debit_amounts[i] if i < len(debit_amounts) else "",
            "credit_amount": credit_amounts[i] if i < len(credit_amounts) else "",
            "memo": line_memos[i] if i < len(line_memos) else "",
        }
        for i in range(len(account_ids))
    ]

    accounts = account_service.list_accounts(db, include_inactive=False)

    def render_error(message: str):
        return templates.TemplateResponse(
            request,
            "journal_entries/form.html",
            {
                "current_user": current_user,
                "accounts": accounts,
                "entry_date": entry_date_raw,
                "memo": memo,
                "rows": rows,
                "error": message,
            },
            status_code=422,
        )

    try:
        entry_date = date_cls.fromisoformat(entry_date_raw)
    except ValueError:
        return render_error("Invalid entry date")

    if not memo:
        return render_error("Memo is required")

    try:
        lines = []
        for row in rows:
            if not row["account_id"]:
                continue
            lines.append(
                LineInput(
                    account_id=int(row["account_id"]),
                    debit_amount=_parse_decimal(row["debit_amount"]),
                    credit_amount=_parse_decimal(row["credit_amount"]),
                    memo=row["memo"].strip() or None,
                )
            )
        entry, _created = journal_service.post_entry(
            db,
            entry_date=entry_date,
            memo=memo,
            lines=lines,
            source="manual",
            created_by_user_id=current_user.id,
        )
    except InvalidJournalEntryError as exc:
        return render_error(str(exc))

    return RedirectResponse(url=f"/journal-entries/{entry.id}?ok=Journal entry posted", status_code=303)


@router.get("/{entry_id}")
def journal_entry_detail(
    request: Request,
    entry_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    entry = journal_service.get_entry(db, entry_id)
    if not entry:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "journal_entries/detail.html", {"current_user": current_user, "entry": entry}
    )
