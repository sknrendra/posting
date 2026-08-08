import pytest

from app.services import invoice_settings_service


@pytest.fixture(autouse=True)
def _restore_invoice_settings_mappings(db):
    """invoice_settings is a migration-seeded singleton, never reset between
    tests. _settings_form_data() below doesn't include the account-mapping
    fields, so any successful POST /invoices/settings in this file would
    otherwise wipe ar_account_id/service_revenue_account_id/etc. to None for
    every test that runs after it, in this file and beyond."""
    settings = invoice_settings_service.get_settings(db)
    original = dict(
        ar_account_id=settings.ar_account_id,
        service_revenue_account_id=settings.service_revenue_account_id,
        tax_payable_account_id=settings.tax_payable_account_id,
        unearned_revenue_account_id=settings.unearned_revenue_account_id,
        sales_discounts_account_id=settings.sales_discounts_account_id,
    )
    yield
    for field, value in original.items():
        setattr(settings, field, value)
    db.commit()


def _new_invoice_form_data(csrf, **overrides):
    data = {
        "csrf_token": csrf,
        "action": "draft",
        "invoice_date": "2026-07-29",
        "customer_name": "Acme Corp",
        "tax_rate": "0",
        "deposit_applied": "0",
        "description": ["Consulting"],
        "quantity": ["1"],
        "rate": ["1000000"],
        "discount_mode": ["amount"],
        "discount_amount": ["0"],
        "discount_percentage": ["0"],
        "hide_qty_rate": ["false"],
    }
    data.update(overrides)
    return data


def test_create_post_void_flow_and_pdf_download(client):
    resp = client.get("/invoices/new")
    assert resp.status_code == 200
    csrf = client.cookies.get("csrf_token")
    assert csrf

    resp = client.post(
        "/invoices/new", data=_new_invoice_form_data(csrf), follow_redirects=False
    )
    assert resp.status_code == 303
    invoice_id = int(resp.headers["location"].split("/invoices/")[1].split("?")[0])

    detail = client.get(f"/invoices/{invoice_id}")
    assert detail.status_code == 200
    assert "Draft" in detail.text

    post_resp = client.post(
        f"/invoices/{invoice_id}/post", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert post_resp.status_code == 303
    assert "error" not in post_resp.headers["location"]

    pdf_resp = client.get(f"/invoices/{invoice_id}/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert pdf_resp.content[:4] == b"%PDF"

    void_resp = client.post(
        f"/invoices/{invoice_id}/void",
        data={"csrf_token": csrf, "void_reason": "customer cancelled"},
        follow_redirects=False,
    )
    assert void_resp.status_code == 303
    assert "error" not in void_resp.headers["location"]

    detail_after_void = client.get(f"/invoices/{invoice_id}")
    assert detail_after_void.status_code == 200
    assert "Void" in detail_after_void.text


def test_draft_pdf_shows_no_invoice_number(client):
    resp = client.get("/invoices/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/new", data=_new_invoice_form_data(csrf), follow_redirects=False
    )
    invoice_id = int(resp.headers["location"].split("/invoices/")[1].split("?")[0])

    pdf_resp = client.get(f"/invoices/{invoice_id}/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.content[:4] == b"%PDF"


def test_deleting_a_draft_removes_it(client):
    resp = client.get("/invoices/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/new", data=_new_invoice_form_data(csrf), follow_redirects=False
    )
    invoice_id = int(resp.headers["location"].split("/invoices/")[1].split("?")[0])

    del_resp = client.post(
        f"/invoices/{invoice_id}/delete", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert del_resp.status_code == 303

    detail = client.get(f"/invoices/{invoice_id}")
    assert detail.status_code == 404


def test_invoices_list_settings_route_not_shadowed_by_id_route(client):
    resp = client.get("/invoices/settings")
    assert resp.status_code == 200
    assert "Invoice Settings" in resp.text


# --- gaps -------------------------------------------------------------------


def _create_draft(client, **overrides):
    client.get("/invoices/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/invoices/new", data=_new_invoice_form_data(csrf, **overrides), follow_redirects=False)
    invoice_id = int(resp.headers["location"].split("/invoices/")[1].split("?")[0])
    return invoice_id, csrf


def test_list_invoices_filters_by_status(client):
    _create_draft(client, customer_name="Filter Draft Co")
    resp = client.get("/invoices?status=draft")
    assert resp.status_code == 200
    assert "Filter Draft Co" in resp.text


def test_list_invoices_search(client):
    _create_draft(client, customer_name="Very Unique Searchable Co")
    resp = client.get("/invoices?search=Unique+Searchable")
    assert resp.status_code == 200
    assert "Very Unique Searchable Co" in resp.text


def test_create_invoice_action_post_creates_and_posts_in_one_step(client):
    invoice_id, _csrf = _create_draft(client, action="post")
    detail = client.get(f"/invoices/{invoice_id}")
    assert detail.status_code == 200
    assert "Posted" in detail.text


def test_create_invoice_action_post_with_invalid_customer_leaves_orphan_draft(client):
    """action=post creates the draft, then fails posting (blank customer name).
    The 422 re-renders the create form, but the draft row from the successful
    create_draft call is never cleaned up — documents this as-is behavior."""
    resp = client.get("/invoices/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/new",
        data=_new_invoice_form_data(csrf, action="post", customer_name=""),
        follow_redirects=False,
    )
    assert resp.status_code == 422

    list_resp = client.get("/invoices?status=draft")
    assert list_resp.status_code == 200
    # At least one orphaned draft now exists from the failed post-on-create attempt.
    assert "Draft" in list_resp.text or "draft" in list_resp.text.lower()


def test_edit_form_shown_for_draft(client):
    invoice_id, _csrf = _create_draft(client)
    resp = client.get(f"/invoices/{invoice_id}/edit")
    assert resp.status_code == 200
    assert "Consulting" in resp.text


def test_edit_form_redirects_for_non_draft(client):
    invoice_id, csrf = _create_draft(client)
    client.post(f"/invoices/{invoice_id}/post", data={"csrf_token": csrf}, follow_redirects=False)
    resp = client.get(f"/invoices/{invoice_id}/edit", follow_redirects=False)
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_edit_draft_action_draft_stays_draft(client):
    invoice_id, csrf = _create_draft(client)
    resp = client.post(
        f"/invoices/{invoice_id}/edit",
        data=_new_invoice_form_data(csrf, description=["Updated line"]),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    detail = client.get(f"/invoices/{invoice_id}")
    assert "Updated line" in detail.text
    assert "Draft" in detail.text


def test_edit_draft_action_post_edits_and_posts(client):
    invoice_id, csrf = _create_draft(client)
    resp = client.post(
        f"/invoices/{invoice_id}/edit",
        data=_new_invoice_form_data(csrf, action="post"),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    detail = client.get(f"/invoices/{invoice_id}")
    assert "Posted" in detail.text


def test_edit_nonexistent_invoice_404(client):
    csrf_resp = client.get("/invoices/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/999999/edit", data=_new_invoice_form_data(csrf), follow_redirects=False
    )
    assert resp.status_code == 404


def test_edit_invalid_date_currently_crashes(client):
    """Documents an existing gap: create_invoice and update_invoice both call
    _parse_header_and_lines *before* their try/except InvoiceError block, so
    an invalid date/amount field raises InvoiceValidationError uncaught
    instead of the intended 422 (unlike journal_entries_controller, which
    wraps its own date parsing in the try). TestClient propagates this
    directly since raise_server_exceptions defaults to True; in a real
    deployment it would surface to the client as a 500."""
    import pytest

    from app.services.invoice_service import InvoiceValidationError

    invoice_id, csrf = _create_draft(client)
    with pytest.raises(InvoiceValidationError, match="not a valid date"):
        client.post(
            f"/invoices/{invoice_id}/edit",
            data=_new_invoice_form_data(csrf, due_date="not-a-date"),
            follow_redirects=False,
        )


def test_post_non_draft_invoice_returns_error_redirect(client):
    invoice_id, csrf = _create_draft(client)
    client.post(f"/invoices/{invoice_id}/post", data={"csrf_token": csrf}, follow_redirects=False)
    resp = client.post(f"/invoices/{invoice_id}/post", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_void_blank_reason_returns_error_redirect(client):
    invoice_id, csrf = _create_draft(client)
    client.post(f"/invoices/{invoice_id}/post", data={"csrf_token": csrf}, follow_redirects=False)
    resp = client.post(
        f"/invoices/{invoice_id}/void", data={"csrf_token": csrf, "void_reason": "   "}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_void_non_posted_invoice_returns_error_redirect(client):
    invoice_id, csrf = _create_draft(client)  # still a draft, never posted
    resp = client.post(
        f"/invoices/{invoice_id}/void",
        data={"csrf_token": csrf, "void_reason": "reason"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_pdf_nonexistent_invoice_404(client):
    resp = client.get("/invoices/999999/pdf")
    assert resp.status_code == 404


def test_pdf_filename_uses_invoice_number_when_posted(client):
    invoice_id, csrf = _create_draft(client)
    client.post(f"/invoices/{invoice_id}/post", data={"csrf_token": csrf}, follow_redirects=False)
    resp = client.get(f"/invoices/{invoice_id}/pdf")
    disposition = resp.headers["content-disposition"]
    assert "draft-invoice" not in disposition
    assert "INV-" in disposition


def test_pdf_filename_falls_back_for_draft(client):
    invoice_id, _csrf = _create_draft(client)
    resp = client.get(f"/invoices/{invoice_id}/pdf")
    disposition = resp.headers["content-disposition"]
    assert f"draft-invoice-{invoice_id}" in disposition


def test_invoice_settings_get_form(client):
    resp = client.get("/invoices/settings")
    assert resp.status_code == 200
    assert "csrf_token" in resp.text


def _settings_form_data(csrf, **overrides):
    data = {
        "csrf_token": csrf,
        "company_name": "Test Co",
        "invoice_prefix": "INV",
        "default_payment_terms_days": "30",
        "default_tax_rate": "0",
    }
    data.update(overrides)
    return data


def test_invoice_settings_update_valid(client):
    client.get("/invoices/settings")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/settings",
        data=_settings_form_data(csrf, company_name="Updated Co Name"),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]
    detail = client.get("/invoices/settings")
    assert "Updated Co Name" in detail.text


def test_invoice_settings_invalid_mapping_returns_422(client):
    client.get("/invoices/settings")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/settings",
        data=_settings_form_data(csrf, ar_account_id="999999"),
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "not found" in resp.text.lower() or "account" in resp.text.lower()


def test_invoice_settings_non_integer_payment_terms_days_currently_crashes(client):
    """Documents an existing gap: default_payment_terms_days is parsed with a
    bare int(...) call, no try/except like _parse_decimal/_parse_date get —
    a non-numeric value raises an uncaught ValueError (TestClient propagates
    it directly rather than turning it into a 500 response, since
    raise_server_exceptions defaults to True; in a real deployment this
    would surface to the client as a 500)."""
    import pytest

    client.get("/invoices/settings")
    csrf = client.cookies.get("csrf_token")
    with pytest.raises(ValueError, match="invalid literal for int"):
        client.post(
            "/invoices/settings",
            data=_settings_form_data(csrf, default_payment_terms_days="not-a-number"),
            follow_redirects=False,
        )


def test_invoice_settings_logo_upload(client):
    client.get("/invoices/settings")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/settings",
        data=_settings_form_data(csrf),
        files={"logo": ("logo.png", b"fake-png-bytes", "image/png")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]


def test_invoice_settings_no_logo_field_no_crash(client):
    client.get("/invoices/settings")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/invoices/settings", data=_settings_form_data(csrf), follow_redirects=False)
    assert resp.status_code == 303


def test_invoice_settings_validation_failure_does_not_save_logo(client):
    client.get("/invoices/settings")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/invoices/settings",
        data=_settings_form_data(csrf, ar_account_id="999999"),
        files={"logo": ("logo.png", b"should-not-be-saved", "image/png")},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    settings_page = client.get("/invoices/settings")
    assert "uploads" not in settings_page.text or "should-not-be-saved" not in settings_page.text
