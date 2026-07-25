from datetime import date as date_cls

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_login
from app.models.user import User
from app.services import trial_balance_service
from app.templating import templates

router = APIRouter(prefix="/balance")


@router.get("/trial-balance")
def trial_balance(
    request: Request,
    as_of: str | None = None,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    as_of_date = date_cls.fromisoformat(as_of) if as_of else date_cls.today()
    result = trial_balance_service.compute(db, as_of_date)
    return templates.TemplateResponse(
        request,
        "balance/trial_balance.html",
        {"current_user": current_user, "as_of": as_of_date.isoformat(), **result},
    )
