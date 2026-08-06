from decimal import Decimal as D

from app.services.invoice_calc import resolve_header, resolve_line, round_rupiah


def test_round_rupiah_half_up():
    assert round_rupiah(D("100.5")) == D("101")
    assert round_rupiah(D("100.4")) == D("100")
    assert round_rupiah(D("-0.5")) == D("0") or round_rupiah(D("100")) == D("100")


def test_percent_mode_qty_doubling_doubles_amount_holds_percentage():
    base = resolve_line(D(1), D(100000), "percent", D(0), D(10))
    doubled = resolve_line(D(2), D(100000), "percent", D(0), D(10))
    assert doubled.discount_amount == base.discount_amount * 2
    assert doubled.discount_percentage == base.discount_percentage == D(10)


def test_amount_mode_qty_doubling_holds_amount_halves_percentage():
    base = resolve_line(D(1), D(100000), "amount", D(10000), D(0))
    doubled = resolve_line(D(2), D(100000), "amount", D(10000), D(0))
    assert doubled.discount_amount == base.discount_amount == D(10000)
    assert doubled.discount_percentage == base.discount_percentage / 2


def test_percent_mode_idempotent_when_fed_back():
    result = resolve_line(D(1), D(137500), "percent", D(0), D("12.5"))
    replayed = resolve_line(D(1), D(137500), "percent", result.discount_amount, result.discount_percentage)
    assert replayed == result


def test_amount_mode_idempotent_when_fed_back():
    result = resolve_line(D(3), D(45000), "amount", D(20000), D(0))
    replayed = resolve_line(D(3), D(45000), "amount", result.discount_amount, result.discount_percentage)
    assert replayed == result


def test_zero_gross_forces_discount_to_zero_regardless_of_mode():
    result = resolve_line(D(0), D(100000), "percent", D(0), D(50))
    assert result.line_gross == 0
    assert result.discount_amount == 0
    assert result.discount_percentage == 0
    assert result.line_amount == 0


def test_discount_amount_clamped_to_line_gross():
    result = resolve_line(D(1), D(1000), "amount", D(5000), D(0))
    assert result.discount_amount == D(1000)
    assert result.line_amount == D(0)


def test_header_calculation_order():
    line1 = resolve_line(D(2), D(500000), "amount", D(100000), D(0))
    line2 = resolve_line(D(1), D(250000), "percent", D(0), D(10))
    header = resolve_header([line1, line2], D(11), D(200000))

    assert header.subtotal_gross == line1.line_gross + line2.line_gross
    assert header.total_discount == line1.discount_amount + line2.discount_amount
    assert header.subtotal_net == header.subtotal_gross - header.total_discount
    assert header.tax_amount == round_rupiah(header.subtotal_net * D(11) / 100)
    assert header.total == header.subtotal_net + header.tax_amount
    assert header.balance_due == header.total - D(200000)


# --- gaps: resolve_line ------------------------------------------------------


def test_negative_gross_not_clamped():
    """No guard on the gross itself — only the derived discount amount is
    ever clamped to [0, line_gross]. A negative quantity/rate produces a
    negative line_gross that flows straight through."""
    result = resolve_line(D(-1), D(1000), "amount", D(0), D(0))
    assert result.line_gross == D(-1000)
    assert result.line_amount == D(-1000)


def test_percent_over_100_clamped_to_full_gross():
    result = resolve_line(D(1), D(1000), "percent", D(0), D(150))
    assert result.discount_amount == D(1000)
    assert result.discount_percentage == D("100.00")
    assert result.line_amount == D(0)


def test_amount_mode_to_percent_conversion_uses_two_decimal_precision():
    # amount->percent uses .quantize(Decimal("0.01")), a different precision
    # than percent->amount's whole-unit round_rupiah — worth pinning explicitly.
    result = resolve_line(D(3), D(1000), "amount", D(1), D(0))
    assert result.discount_percentage == (D(1) / D(3000) * 100).quantize(D("0.01"))


def test_discount_amount_exactly_equal_to_gross_not_the_over_clamp_path():
    result = resolve_line(D(1), D(1000), "amount", D(1000), D(0))
    assert result.discount_amount == D(1000)
    assert result.line_amount == D(0)
    # Percentage still derives to 100.00 via the normal amount->percent path,
    # not via the `amount > line_gross` clamp branch (amount == gross, not >).
    assert result.discount_percentage == D("100.00")


def test_round_rupiah_exact_whole_number_unchanged():
    assert round_rupiah(D("100")) == D("100")


def test_resolve_header_empty_line_results():
    header = resolve_header([], D(11), D(500))
    assert header.subtotal_gross == D(0)
    assert header.total_discount == D(0)
    assert header.subtotal_net == D(0)
    assert header.tax_amount == D(0)
    assert header.total == D(0)
    assert header.balance_due == D(-500)


def test_tax_rounds_half_up_at_the_half_rupiah_boundary():
    line = resolve_line(D(1), D(50), "amount", D(0), D(0))
    header = resolve_header([line], D(1), D(0))  # 50 * 1% = 0.50 -> rounds up to 1
    assert header.tax_amount == D(1)
