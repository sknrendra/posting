from datetime import date as date_cls

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_login, verify_csrf
from app.models.user import User
from app.services import reconciliation_service, report_service
from app.services.report_service import ReportGenerationError
from app.templating import templates
from app.utils import periods as period_utils

router = APIRouter(prefix="/reports")

REPORT_TYPES = ("profit_loss", "balance_sheet", "cash_flow")
REPORT_LABELS = {
    "profit_loss": "Profit & Loss",
    "balance_sheet": "Balance Sheet",
    "cash_flow": "Cash Flow",
}


@router.get("")
def reports_index(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    tables = {}
    for period_type in period_utils.PERIOD_TYPES:
        rows = []
        for start, end in reconciliation_service.list_periods(db, period_type):
            recon_status = reconciliation_service.get_period_status(db, period_type, start, end)
            has_reports = (
                report_service.get_generated_report(db, "profit_loss", period_type, start, end)
                is not None
            )
            rows.append(
                {
                    "period_start": start,
                    "period_end": end,
                    "reconciliation_complete": recon_status["is_complete"],
                    "has_reports": has_reports,
                }
            )
        tables[period_type] = rows
    return templates.TemplateResponse(
        request,
        "reports/index.html",
        {
            "current_user": current_user,
            "tables": tables,
            "period_types": period_utils.PERIOD_TYPES,
            "report_types": REPORT_TYPES,
        },
    )


@router.post("/generate", dependencies=[Depends(verify_csrf)])
async def generate_reports(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    form = await request.form()
    period_type = str(form.get("period_type", ""))
    period_start_raw = str(form.get("period_start", ""))
    if period_type not in period_utils.PERIOD_TYPES:
        raise HTTPException(status_code=404)
    start_date = date_cls.fromisoformat(period_start_raw)
    start, end = period_utils.period_bounds(period_type, start_date)
    try:
        report_service.generate_reports(db, period_type, start, end, current_user)
    except ReportGenerationError as exc:
        return RedirectResponse(url=f"/reports?error={exc}", status_code=303)
    return RedirectResponse(url="/reports?ok=Reports generated", status_code=303)


@router.get("/{report_type}/{period_type}/{period_start}")
def view_report(
    request: Request,
    report_type: str,
    period_type: str,
    period_start: str,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    if report_type not in REPORT_TYPES or period_type not in period_utils.PERIOD_TYPES:
        raise HTTPException(status_code=404)
    start_date = date_cls.fromisoformat(period_start)
    start, end = period_utils.period_bounds(period_type, start_date)
    report = report_service.get_generated_report(db, report_type, period_type, start, end)
    if not report:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        f"reports/{report_type}.html",
        {
            "current_user": current_user,
            "report": report,
            "data": report.data,
            "period_type": period_type,
            "period_start": start,
            "period_end": end,
            "label": REPORT_LABELS[report_type],
        },
    )
