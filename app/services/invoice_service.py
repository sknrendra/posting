from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session as DbSession

from app.models.base import utcnow
from app.models.invoice import Invoice, InvoiceLine
from app.models.invoice_number_counter import InvoiceNumberCounter
from app.models.journal_entry import JournalEntry
from app.services import invoice_calc, invoice_settings_service, journal_service
from app.services.journal_service import LineInput


class InvoiceError(Exception):
    pass


class InvoiceNotDraftError(InvoiceError):
    pass


class InvoiceNotPostedError(InvoiceError):
    pass


class InvoiceValidationError(InvoiceError):
    pass


class MissingAccountMappingError(InvoiceError):
    pass


@dataclass
class InvoiceLineInput:
    description: str
    quantity: Decimal = Decimal("1")
    rate: Decimal = Decimal("0")
    discount_mode: str = "amount"
    discount_amount: Decimal = Decimal("0")
    discount_percentage: Decimal = Decimal("0")
    hide_qty_rate: bool = False


@dataclass
class InvoiceHeaderInput:
    invoice_date: date
    due_date: date | None = None
    service_period_start: date | None = None
    service_period_end: date | None = None
    po_number: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    customer_contact: str | None = None
    tax_rate: Decimal = Decimal("0")
    deposit_applied: Decimal = Decimal("0")
    notes: str | None = None
    terms: str | None = None
    lines: list[InvoiceLineInput] = field(default_factory=list)


def _apply_lines(invoice: Invoice, line_inputs: list[InvoiceLineInput]) -> list[invoice_calc.LineResult]:
    invoice.lines.clear()
    results = []
    for i, li in enumerate(line_inputs, start=1):
        result = invoice_calc.resolve_line(
            li.quantity, li.rate, li.discount_mode, li.discount_amount, li.discount_percentage
        )
        results.append(result)
        invoice.lines.append(
            InvoiceLine(
                line_number=i,
                description=li.description,
                quantity=li.quantity,
                rate=li.rate,
                discount_mode=li.discount_mode,
                discount_amount=result.discount_amount,
                discount_percentage=result.discount_percentage,
                line_gross=result.line_gross,
                line_amount=result.line_amount,
                hide_qty_rate=li.hide_qty_rate,
            )
        )
    return results


def _recalculate_header(invoice: Invoice, results: list[invoice_calc.LineResult]) -> None:
    header = invoice_calc.resolve_header(results, invoice.tax_rate, invoice.deposit_applied)
    invoice.subtotal_gross = header.subtotal_gross
    invoice.total_discount = header.total_discount
    invoice.subtotal_net = header.subtotal_net
    invoice.tax_amount = header.tax_amount
    invoice.total = header.total
    invoice.balance_due = header.balance_due
    if invoice.deposit_applied > invoice.total:
        raise InvoiceValidationError("Deposit applied cannot exceed the invoice total")


def _apply_header_fields(invoice: Invoice, header: InvoiceHeaderInput) -> None:
    invoice.invoice_date = header.invoice_date
    invoice.due_date = header.due_date
    invoice.service_period_start = header.service_period_start
    invoice.service_period_end = header.service_period_end
    invoice.po_number = header.po_number
    invoice.customer_name = header.customer_name
    invoice.customer_address = header.customer_address
    invoice.customer_contact = header.customer_contact
    invoice.tax_rate = header.tax_rate
    invoice.deposit_applied = header.deposit_applied
    invoice.notes = header.notes
    invoice.terms = header.terms


def list_invoices(db: DbSession, status: str | None = None, search: str | None = None) -> list[Invoice]:
    query = db.query(Invoice)
    if status:
        query = query.filter(Invoice.status == status)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(Invoice.invoice_number.ilike(like), Invoice.customer_name.ilike(like))
        )
    return query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all()


def get_invoice(db: DbSession, invoice_id: int) -> Invoice | None:
    return db.get(Invoice, invoice_id)


def get_invoice_by_journal_entry_id(db: DbSession, entry_id: int) -> Invoice | None:
    return (
        db.query(Invoice)
        .filter(or_(Invoice.journal_entry_id == entry_id, Invoice.void_journal_entry_id == entry_id))
        .first()
    )


def create_draft(db: DbSession, header: InvoiceHeaderInput, user_id: int | None) -> Invoice:
    invoice = Invoice(status="draft", created_by_user_id=user_id)
    _apply_header_fields(invoice, header)
    results = _apply_lines(invoice, header.lines)
    _recalculate_header(invoice, results)
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


def update_draft(db: DbSession, invoice: Invoice, header: InvoiceHeaderInput) -> Invoice:
    if invoice.status != "draft":
        raise InvoiceNotDraftError("Only draft invoices can be edited")
    # Flush the old lines' deletion before _apply_lines re-appends new ones —
    # otherwise SQLAlchemy can emit the new INSERTs before the old rows are
    # gone, colliding on the (invoice_id, line_number) unique constraint. This
    # must happen before _apply_header_fields mutates the invoice row itself,
    # so an invalid header (e.g. deposit > total) still fails via the clean
    # InvoiceValidationError in _recalculate_header below rather than tripping
    # the DB's CHECK constraint on a premature flush.
    invoice.lines.clear()
    db.flush()
    _apply_header_fields(invoice, header)
    results = _apply_lines(invoice, header.lines)
    _recalculate_header(invoice, results)
    db.commit()
    db.refresh(invoice)
    return invoice


def delete_draft(db: DbSession, invoice: Invoice) -> None:
    if invoice.status != "draft":
        raise InvoiceNotDraftError("Only draft invoices can be deleted")
    db.delete(invoice)
    db.commit()


def _assign_next_sequence(db: DbSession, invoice_date_: date) -> int:
    stmt = (
        sqlite_insert(InvoiceNumberCounter)
        .values(invoice_date=invoice_date_, next_seq=1)
        .on_conflict_do_update(
            index_elements=["invoice_date"],
            set_={"next_seq": InvoiceNumberCounter.next_seq + 1},
        )
        .returning(InvoiceNumberCounter.next_seq)
    )
    return db.execute(stmt).scalar_one()


def _format_invoice_number(prefix: str, invoice_date_: date, seq: int) -> str:
    return f"{prefix}-{invoice_date_.strftime('%Y%m%d')}-{seq:04d}"


def _line_or_none(account_id: int, debit: Decimal, credit: Decimal, memo: str) -> LineInput | None:
    if debit == 0 and credit == 0:
        return None
    return LineInput(account_id=account_id, debit_amount=debit, credit_amount=credit, memo=memo)


def post_invoice(db: DbSession, invoice: Invoice, user_id: int | None) -> Invoice:
    if invoice.status != "draft":
        raise InvoiceNotDraftError("Only draft invoices can be posted")
    if not invoice.lines:
        raise InvoiceValidationError("Invoice must have at least one line to post")
    if not (invoice.customer_name and invoice.customer_name.strip()):
        raise InvoiceValidationError("Customer name is required to post an invoice")

    # Never trust stale stored totals — re-derive everything from the raw line inputs.
    results = []
    for line in sorted(invoice.lines, key=lambda l: l.line_number):
        result = invoice_calc.resolve_line(
            line.quantity, line.rate, line.discount_mode, line.discount_amount, line.discount_percentage
        )
        line.line_gross = result.line_gross
        line.discount_amount = result.discount_amount
        line.discount_percentage = result.discount_percentage
        line.line_amount = result.line_amount
        results.append(result)
    _recalculate_header(invoice, results)

    settings = invoice_settings_service.get_settings(db)
    if settings.ar_account_id is None:
        raise MissingAccountMappingError(
            "Accounts Receivable account is not configured in Invoice Settings"
        )
    if settings.service_revenue_account_id is None:
        raise MissingAccountMappingError(
            "Service Revenue account is not configured in Invoice Settings"
        )
    if invoice.tax_amount > 0 and settings.tax_payable_account_id is None:
        raise MissingAccountMappingError(
            "Tax Payable account is not configured in Invoice Settings "
            "(required because this invoice has tax)"
        )
    if invoice.deposit_applied > 0 and settings.unearned_revenue_account_id is None:
        raise MissingAccountMappingError(
            "Unearned Revenue account is not configured in Invoice Settings "
            "(required because this invoice has a deposit applied)"
        )

    method = "gross" if settings.sales_discounts_account_id and invoice.total_discount > 0 else "net"

    try:
        seq = _assign_next_sequence(db, invoice.invoice_date)
        invoice_number = _format_invoice_number(settings.invoice_prefix, invoice.invoice_date, seq)
        memo = f"Invoice {invoice_number}"

        je_lines = [
            _line_or_none(settings.ar_account_id, invoice.balance_due, Decimal(0), memo),
            _line_or_none(settings.unearned_revenue_account_id, invoice.deposit_applied, Decimal(0), memo),
        ]
        if method == "gross":
            je_lines.append(
                _line_or_none(settings.sales_discounts_account_id, invoice.total_discount, Decimal(0), memo)
            )
            je_lines.append(
                _line_or_none(settings.service_revenue_account_id, Decimal(0), invoice.subtotal_gross, memo)
            )
        else:
            je_lines.append(
                _line_or_none(settings.service_revenue_account_id, Decimal(0), invoice.subtotal_net, memo)
            )
        je_lines.append(_line_or_none(settings.tax_payable_account_id, Decimal(0), invoice.tax_amount, memo))
        je_lines = [l for l in je_lines if l is not None]

        entry, _created = journal_service.post_entry(
            db,
            entry_date=invoice.invoice_date,
            memo=memo,
            lines=je_lines,
            source="invoice",
            created_by_user_id=user_id,
            invoice_id=invoice.id,
            commit=False,
        )

        invoice.snapshot_company_name = settings.company_name
        invoice.snapshot_company_address = settings.company_address
        invoice.snapshot_tax_reg_number = settings.company_tax_reg_number
        invoice.snapshot_payment_instructions = invoice_settings_service.compose_payment_instructions(
            settings
        )

        invoice.status = "posted"
        invoice.invoice_number = invoice_number
        invoice.journal_entry_id = entry.id
        invoice.posted_by_user_id = user_id

        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(invoice)
    return invoice


def void_invoice(db: DbSession, invoice: Invoice, void_reason: str, user_id: int | None) -> Invoice:
    if invoice.status != "posted":
        raise InvoiceNotPostedError("Only posted invoices can be voided")
    void_reason = (void_reason or "").strip()
    if not void_reason:
        raise InvoiceValidationError("A void reason is required")

    original_entry = db.get(JournalEntry, invoice.journal_entry_id)
    if original_entry is None:
        raise InvoiceError("The invoice's original journal entry could not be found")

    memo = f"Void of invoice {invoice.invoice_number}"
    reversal_lines = [
        LineInput(
            account_id=jl.account_id,
            debit_amount=jl.credit_amount,
            credit_amount=jl.debit_amount,
            memo=memo,
        )
        for jl in original_entry.lines
    ]

    try:
        entry, _created = journal_service.post_entry(
            db,
            entry_date=date.today(),
            memo=memo,
            lines=reversal_lines,
            source="invoice",
            created_by_user_id=user_id,
            invoice_id=invoice.id,
            reverses_entry_id=original_entry.id,
            commit=False,
        )
        invoice.status = "void"
        invoice.void_reason = void_reason
        invoice.voided_at = utcnow()
        invoice.voided_by_user_id = user_id
        invoice.void_journal_entry_id = entry.id
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(invoice)
    return invoice
