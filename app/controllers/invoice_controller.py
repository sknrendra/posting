import calendar
from datetime import date as date_cls, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session as DbSession

from app.dependencies import get_db, require_login, verify_csrf
from app.models.account import ACCOUNT_TYPES
from app.models.user import User
from app.services import (
    account_service,
    invoice_pdf_service,
    invoice_service,
    invoice_settings_service,
)
from app.services.invoice_service import (
    InvoiceError,
    InvoiceHeaderInput,
    InvoiceLineInput,
    InvoiceValidationError,
)
from app.services.invoice_settings_service import InvalidAccountMappingError
from app.templating import templates

router = APIRouter(prefix="/invoices")
invoice_settings_router = APIRouter(prefix="/invoices/settings")


def _parse_decimal(raw: str, default: str = "0") -> Decimal:
    raw = (raw or "").strip()
    if not raw:
        raw = default
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise InvoiceValidationError(f"'{raw}' is not a valid number")


def _parse_date(raw: str | None) -> date_cls | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date_cls.fromisoformat(raw)
    except ValueError:
        raise InvoiceValidationError(f"'{raw}' is not a valid date")


def _line_to_dict(line) -> dict:
    """Converts an InvoiceLine/InvoiceLineInput into a JSON-serializable dict for
    seeding the form's Alpine.js state (Decimal isn't tojson-serializable)."""
    return {
        "description": line.description,
        "quantity": str(line.quantity),
        "rate": str(line.rate),
        "discount_mode": line.discount_mode,
        "discount_amount": str(line.discount_amount),
        "discount_percentage": str(line.discount_percentage),
        "hide_qty_rate": bool(line.hide_qty_rate),
    }


async def _parse_header_and_lines(request: Request) -> tuple[InvoiceHeaderInput, str]:
    form = await request.form()

    invoice_date = _parse_date(str(form.get("invoice_date", ""))) or date_cls.today()
    due_date = _parse_date(form.get("due_date"))
    service_period_start = _parse_date(form.get("service_period_start"))
    service_period_end = _parse_date(form.get("service_period_end"))

    descriptions = form.getlist("description")
    quantities = form.getlist("quantity")
    rates = form.getlist("rate")
    discount_modes = form.getlist("discount_mode")
    discount_amounts = form.getlist("discount_amount")
    discount_percentages = form.getlist("discount_percentage")
    hide_qty_rates = form.getlist("hide_qty_rate")

    lines = []
    for i in range(len(descriptions)):
        description = descriptions[i].strip()
        if not description:
            continue
        lines.append(
            InvoiceLineInput(
                description=description,
                quantity=_parse_decimal(quantities[i] if i < len(quantities) else "", "1"),
                rate=_parse_decimal(rates[i] if i < len(rates) else "", "0"),
                discount_mode=discount_modes[i] if i < len(discount_modes) else "amount",
                discount_amount=_parse_decimal(
                    discount_amounts[i] if i < len(discount_amounts) else "", "0"
                ),
                discount_percentage=_parse_decimal(
                    discount_percentages[i] if i < len(discount_percentages) else "", "0"
                ),
                hide_qty_rate=(hide_qty_rates[i] if i < len(hide_qty_rates) else "false") == "true",
            )
        )

    return InvoiceHeaderInput(
        invoice_date=invoice_date,
        due_date=due_date,
        service_period_start=service_period_start,
        service_period_end=service_period_end,
        po_number=str(form.get("po_number", "")).strip() or None,
        customer_name=str(form.get("customer_name", "")).strip() or None,
        customer_address=str(form.get("customer_address", "")).strip() or None,
        customer_contact=str(form.get("customer_contact", "")).strip() or None,
        tax_rate=_parse_decimal(form.get("tax_rate"), "0"),
        deposit_applied=_parse_decimal(form.get("deposit_applied"), "0"),
        notes=str(form.get("notes", "")).strip() or None,
        terms=str(form.get("terms", "")).strip() or None,
        lines=lines,
    ), str(form.get("action", "draft"))


@router.get("")
def list_invoices(
    request: Request,
    status: str | None = None,
    search: str | None = None,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoices = invoice_service.list_invoices(db, status=status or None, search=search or None)
    return templates.TemplateResponse(
        request,
        "invoices/list.html",
        {
            "current_user": current_user,
            "invoices": invoices,
            "status": status or "",
            "search": search or "",
        },
    )


@router.get("/new")
def new_invoice_form(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    settings = invoice_settings_service.get_settings(db)
    today = date_cls.today()
    default_due = today + timedelta(days=settings.default_payment_terms_days)
    month_end_day = calendar.monthrange(today.year, today.month)[1]
    return templates.TemplateResponse(
        request,
        "invoices/form.html",
        {
            "current_user": current_user,
            "invoice": None,
            "invoice_date": today.isoformat(),
            "due_date": default_due.isoformat(),
            "service_period_start": today.replace(day=1).isoformat(),
            "service_period_end": today.replace(day=month_end_day).isoformat(),
            "tax_rate": str(settings.default_tax_rate),
            "notes": settings.default_notes or "",
            "terms": settings.default_terms or "",
            "lines": [],
            "default_payment_terms_days": settings.default_payment_terms_days,
        },
    )


@router.post("/new", dependencies=[Depends(verify_csrf)])
async def create_invoice(
    request: Request,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    header, action = await _parse_header_and_lines(request)
    try:
        invoice = invoice_service.create_draft(db, header, current_user.id)
        if action == "post":
            invoice = invoice_service.post_invoice(db, invoice, current_user.id)
    except InvoiceError as exc:
        settings = invoice_settings_service.get_settings(db)
        return templates.TemplateResponse(
            request,
            "invoices/form.html",
            {
                "current_user": current_user,
                "invoice": None,
                "invoice_date": header.invoice_date.isoformat(),
                "due_date": header.due_date.isoformat() if header.due_date else "",
                "service_period_start": header.service_period_start.isoformat()
                if header.service_period_start
                else "",
                "service_period_end": header.service_period_end.isoformat()
                if header.service_period_end
                else "",
                "tax_rate": str(header.tax_rate),
                "notes": header.notes or "",
                "terms": header.terms or "",
                "lines": [_line_to_dict(l) for l in header.lines],
                "default_payment_terms_days": settings.default_payment_terms_days,
                "error": str(exc),
            },
            status_code=422,
        )
    return RedirectResponse(url=f"/invoices/{invoice.id}?ok=Invoice saved", status_code=303)


@router.get("/{invoice_id}")
def invoice_detail(
    request: Request,
    invoice_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoice = invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "invoices/detail.html", {"current_user": current_user, "invoice": invoice}
    )


@router.get("/{invoice_id}/edit")
def edit_invoice_form(
    request: Request,
    invoice_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoice = invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404)
    if invoice.status != "draft":
        return RedirectResponse(
            url=f"/invoices/{invoice.id}?error=Only draft invoices can be edited", status_code=303
        )
    settings = invoice_settings_service.get_settings(db)
    return templates.TemplateResponse(
        request,
        "invoices/form.html",
        {
            "current_user": current_user,
            "invoice": invoice,
            "invoice_date": invoice.invoice_date.isoformat(),
            "due_date": invoice.due_date.isoformat() if invoice.due_date else "",
            "service_period_start": invoice.service_period_start.isoformat()
            if invoice.service_period_start
            else "",
            "service_period_end": invoice.service_period_end.isoformat()
            if invoice.service_period_end
            else "",
            "tax_rate": str(invoice.tax_rate),
            "notes": invoice.notes or "",
            "terms": invoice.terms or "",
            "lines": [_line_to_dict(l) for l in invoice.lines],
            "default_payment_terms_days": settings.default_payment_terms_days,
        },
    )


@router.post("/{invoice_id}/edit", dependencies=[Depends(verify_csrf)])
async def update_invoice(
    request: Request,
    invoice_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoice = invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404)
    header, action = await _parse_header_and_lines(request)
    try:
        invoice = invoice_service.update_draft(db, invoice, header)
        if action == "post":
            invoice = invoice_service.post_invoice(db, invoice, current_user.id)
    except InvoiceError as exc:
        settings = invoice_settings_service.get_settings(db)
        return templates.TemplateResponse(
            request,
            "invoices/form.html",
            {
                "current_user": current_user,
                "invoice": invoice,
                "invoice_date": header.invoice_date.isoformat(),
                "due_date": header.due_date.isoformat() if header.due_date else "",
                "service_period_start": header.service_period_start.isoformat()
                if header.service_period_start
                else "",
                "service_period_end": header.service_period_end.isoformat()
                if header.service_period_end
                else "",
                "tax_rate": str(header.tax_rate),
                "notes": header.notes or "",
                "terms": header.terms or "",
                "lines": [_line_to_dict(l) for l in header.lines],
                "default_payment_terms_days": settings.default_payment_terms_days,
                "error": str(exc),
            },
            status_code=422,
        )
    return RedirectResponse(url=f"/invoices/{invoice.id}?ok=Invoice saved", status_code=303)


@router.post("/{invoice_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_invoice(
    invoice_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoice = invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404)
    try:
        invoice_service.delete_draft(db, invoice)
    except InvoiceError as exc:
        return RedirectResponse(url=f"/invoices/{invoice_id}?error={exc}", status_code=303)
    return RedirectResponse(url="/invoices?ok=Invoice deleted", status_code=303)


@router.post("/{invoice_id}/post", dependencies=[Depends(verify_csrf)])
def post_invoice_route(
    invoice_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoice = invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404)
    try:
        invoice_service.post_invoice(db, invoice, current_user.id)
    except InvoiceError as exc:
        return RedirectResponse(url=f"/invoices/{invoice_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/invoices/{invoice_id}?ok=Invoice posted", status_code=303)


@router.post("/{invoice_id}/void", dependencies=[Depends(verify_csrf)])
async def void_invoice_route(
    request: Request,
    invoice_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoice = invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404)
    form = await request.form()
    void_reason = str(form.get("void_reason", ""))
    try:
        invoice_service.void_invoice(db, invoice, void_reason, current_user.id)
    except InvoiceError as exc:
        return RedirectResponse(url=f"/invoices/{invoice_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/invoices/{invoice_id}?ok=Invoice voided", status_code=303)


@router.get("/{invoice_id}/pdf")
def invoice_pdf(
    invoice_id: int,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    invoice = invoice_service.get_invoice(db, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404)
    settings = invoice_settings_service.get_settings(db)
    pdf_bytes = invoice_pdf_service.render_invoice_pdf(invoice, settings)
    filename = invoice.invoice_number or f"draft-invoice-{invoice.id}"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


@invoice_settings_router.get("")
def invoice_settings_form(
    request: Request, current_user: User = Depends(require_login), db: DbSession = Depends(get_db)
):
    settings = invoice_settings_service.get_settings(db)
    accounts = account_service.list_accounts(db, include_inactive=False)
    return templates.TemplateResponse(
        request,
        "invoice_settings/form.html",
        {"current_user": current_user, "settings": settings, "accounts": accounts},
    )


def _parse_account_id(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


@invoice_settings_router.post("", dependencies=[Depends(verify_csrf)])
async def update_invoice_settings(
    request: Request,
    current_user: User = Depends(require_login),
    db: DbSession = Depends(get_db),
):
    settings = invoice_settings_service.get_settings(db)
    accounts = account_service.list_accounts(db, include_inactive=False)
    form = await request.form()

    def render_error(message: str):
        return templates.TemplateResponse(
            request,
            "invoice_settings/form.html",
            {
                "current_user": current_user,
                "settings": settings,
                "accounts": accounts,
                "error": message,
            },
            status_code=422,
        )

    try:
        settings = invoice_settings_service.update_settings(
            db,
            settings,
            company_name=str(form.get("company_name", "")).strip() or None,
            company_address=str(form.get("company_address", "")).strip() or None,
            company_tax_reg_number=str(form.get("company_tax_reg_number", "")).strip() or None,
            bank_name=str(form.get("bank_name", "")).strip() or None,
            bank_account_name=str(form.get("bank_account_name", "")).strip() or None,
            bank_account_number=str(form.get("bank_account_number", "")).strip() or None,
            accepted_payment_methods=str(form.get("accepted_payment_methods", "")).strip() or None,
            payment_reference_instruction=str(form.get("payment_reference_instruction", "")).strip()
            or None,
            invoice_prefix=str(form.get("invoice_prefix", "INV")).strip() or "INV",
            default_payment_terms_days=int(form.get("default_payment_terms_days") or 30),
            default_tax_rate=_parse_decimal(form.get("default_tax_rate"), "0"),
            default_notes=str(form.get("default_notes", "")).strip() or None,
            default_terms=str(form.get("default_terms", "")).strip() or None,
            ar_account_id=_parse_account_id(form.get("ar_account_id")),
            service_revenue_account_id=_parse_account_id(form.get("service_revenue_account_id")),
            tax_payable_account_id=_parse_account_id(form.get("tax_payable_account_id")),
            unearned_revenue_account_id=_parse_account_id(form.get("unearned_revenue_account_id")),
            sales_discounts_account_id=_parse_account_id(form.get("sales_discounts_account_id")),
        )
    except (InvalidAccountMappingError, InvoiceValidationError) as exc:
        return render_error(str(exc))

    logo = form.get("logo")
    if isinstance(logo, UploadFile) and logo.filename:
        content = await logo.read()
        if content:
            invoice_settings_service.save_logo(settings, logo.filename, content)
            db.commit()

    return RedirectResponse(url="/invoices/settings?ok=Invoice settings saved", status_code=303)
