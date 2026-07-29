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
