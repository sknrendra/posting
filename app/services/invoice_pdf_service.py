from pathlib import Path

from weasyprint import HTML

from app.models.invoice import Invoice
from app.models.invoice_settings import InvoiceSettings
from app.services import invoice_settings_service
from app.templating import templates

# Same icon used next to the "Posting" wordmark in the sidebar (app/templates/layout.html).
# Falls back to this when no custom company logo has been uploaded in Invoice Settings,
# so the PDF header never renders blank.
_DEFAULT_LOGO_PATH = Path("app/static/favicon/android-chrome-192x192.png")


def _logo_file_uri(settings: InvoiceSettings) -> tuple[str | None, bool]:
    """Returns (file_uri, is_default) — is_default is True when falling back to the
    app's own icon (no custom logo configured), used to decide whether to also show
    the "Posting" wordmark alongside it."""
    if settings.company_logo_path:
        # company_logo_path is a web path like "/static/uploads/x.png" served from
        # app/static — map it back to the on-disk file and hand WeasyPrint a
        # file:// URI directly, sidestepping base_url/relative-URL resolution.
        disk_path = Path("app") / settings.company_logo_path.lstrip("/")
        if disk_path.exists():
            return disk_path.resolve().as_uri(), False
    if _DEFAULT_LOGO_PATH.exists():
        return _DEFAULT_LOGO_PATH.resolve().as_uri(), True
    return None, False


def render_invoice_pdf(invoice: Invoice, settings: InvoiceSettings) -> bytes:
    """Renders app/templates/invoices/pdf.html (a self-contained, Tailwind-free
    template) to PDF bytes via WeasyPrint. Posted/void invoices show their
    frozen snapshot_* fields; drafts fall back to live settings so the preview
    looks reasonable before anything is snapshotted."""
    if invoice.status == "draft":
        payment_instructions = invoice_settings_service.compose_payment_instructions(settings)
    else:
        payment_instructions = invoice.snapshot_payment_instructions

    logo_url, using_default_logo = _logo_file_uri(settings)

    template = templates.get_template("invoices/pdf.html")
    html = template.render(
        invoice=invoice,
        settings=settings,
        logo_url=logo_url,
        using_default_logo=using_default_logo,
        payment_instructions=payment_instructions,
    )
    return HTML(string=html).write_pdf()
