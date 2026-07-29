from fastapi.templating import Jinja2Templates


def _csrf_context(request):
    return {"csrf_token": getattr(request.state, "csrf_token", "")}


def rupiah(value) -> str:
    """Formats a whole-rupiah amount as 'Rp 15.000.000' (dot thousands separator)."""
    return "Rp {:,}".format(int(value)).replace(",", ".")


def long_date(value) -> str:
    """Formats a date as 'March 15, 2026'."""
    if value is None:
        return ""
    return value.strftime("%B %-d, %Y")


def qty(value) -> str:
    """Strips trailing zeros from a quantity (e.g. 2.0000 -> '2', 2.5000 -> '2.5')."""
    s = f"{value:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


templates = Jinja2Templates(directory="app/templates", context_processors=[_csrf_context])
templates.env.filters["rupiah"] = rupiah
templates.env.filters["long_date"] = long_date
templates.env.filters["qty"] = qty
