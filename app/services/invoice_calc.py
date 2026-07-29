"""Pure calculation functions for invoices — no db access, no I/O.

The create/edit form mirrors this exact arithmetic in Alpine.js for live preview
(an intentional duplication). The server never trusts client-submitted derived
values (line_gross, totals, etc.) — invoice_service always re-runs these
functions from raw quantity/rate/discount inputs before saving or posting.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


def round_rupiah(value: Decimal) -> Decimal:
    """Whole-rupiah, round-half-up. Deliberately not report_service's rounding
    (which quantizes to 0.01) — IDR here has no subunit."""
    return Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP)


@dataclass
class LineResult:
    line_gross: Decimal
    discount_amount: Decimal
    discount_percentage: Decimal
    line_amount: Decimal


def resolve_line(
    quantity: Decimal,
    rate: Decimal,
    discount_mode: str,
    discount_amount: Decimal,
    discount_percentage: Decimal,
) -> LineResult:
    """Resolves a line's gross/discount/amount. Only ever derives the field the
    mode says is NOT authoritative, from the one authoritative input — this is
    what prevents an amount<->percentage feedback loop, since the edited field
    is never recomputed from its own derived counterpart, and each call fully
    re-derives from (mode, authoritative input) rather than accumulating.
    """
    line_gross = round_rupiah(quantity * rate)

    if line_gross == 0:
        # Percent is undefined against a zero base — force both to zero regardless of mode.
        return LineResult(Decimal(0), Decimal(0), Decimal(0), Decimal(0))

    if discount_mode == "percent":
        pct = discount_percentage
        amount = round_rupiah(line_gross * pct / Decimal(100))
    else:
        amount = discount_amount
        pct = (amount / line_gross * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if amount > line_gross:
        amount = line_gross
        pct = Decimal("100.00")
    if amount < 0:
        amount = Decimal(0)
        pct = Decimal(0)

    line_amount = line_gross - amount
    return LineResult(line_gross, amount, pct, line_amount)


@dataclass
class HeaderResult:
    subtotal_gross: Decimal
    total_discount: Decimal
    subtotal_net: Decimal
    tax_amount: Decimal
    total: Decimal
    balance_due: Decimal


def resolve_header(
    line_results: list[LineResult],
    tax_rate: Decimal,
    deposit_applied: Decimal,
) -> HeaderResult:
    subtotal_gross = sum((r.line_gross for r in line_results), Decimal(0))
    total_discount = sum((r.discount_amount for r in line_results), Decimal(0))
    subtotal_net = subtotal_gross - total_discount
    tax_amount = round_rupiah(subtotal_net * tax_rate / Decimal(100))
    total = subtotal_net + tax_amount
    balance_due = total - deposit_applied
    return HeaderResult(subtotal_gross, total_discount, subtotal_net, tax_amount, total, balance_due)
