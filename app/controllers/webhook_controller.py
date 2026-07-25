from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_api_key
from app.models.api_key import ApiKey
from app.services import account_service, journal_service
from app.services.journal_service import InvalidJournalEntryError, LineInput

router = APIRouter(prefix="/api/v1")


class JournalLineIn(BaseModel):
    account_id: int
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    memo: str | None = None


class JournalEntryIn(BaseModel):
    entry_date: date
    memo: str
    external_reference: str | None = None
    lines: list[JournalLineIn] = Field(min_length=2)


def _serialize_entry(entry) -> dict:
    return {
        "id": entry.id,
        "entry_date": entry.entry_date.isoformat(),
        "memo": entry.memo,
        "source": entry.source,
        "external_reference": entry.external_reference,
        "lines": [
            {
                "account_id": line.account_id,
                "debit_amount": str(line.debit_amount),
                "credit_amount": str(line.credit_amount),
                "memo": line.memo,
            }
            for line in entry.lines
        ],
    }


@router.post("/journal-entries")
def create_journal_entry(
    payload: JournalEntryIn,
    response: Response,
    api_key: ApiKey = Depends(require_api_key),
    db: DbSession = Depends(get_db),
):
    lines = [
        LineInput(
            account_id=line.account_id,
            debit_amount=line.debit_amount,
            credit_amount=line.credit_amount,
            memo=line.memo,
        )
        for line in payload.lines
    ]
    try:
        entry, created = journal_service.post_entry(
            db,
            entry_date=payload.entry_date,
            memo=payload.memo,
            lines=lines,
            source="webhook",
            created_by_api_key_id=api_key.id,
            external_reference=payload.external_reference,
        )
    except InvalidJournalEntryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    response.status_code = 201 if created else 200
    return _serialize_entry(entry)


@router.get("/accounts")
def list_accounts(api_key: ApiKey = Depends(require_api_key), db: DbSession = Depends(get_db)):
    accounts = account_service.list_accounts(db, include_inactive=False)
    return [
        {
            "id": account.id,
            "code": account.code,
            "name": account.name,
            "account_type": account.account_type,
            "is_cash_account": account.is_cash_account,
        }
        for account in accounts
    ]
