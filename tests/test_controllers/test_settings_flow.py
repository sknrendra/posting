import itertools

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import auth_service

_seq = itertools.count()


def _email():
    return f"settingsflow{next(_seq)}@example.com"


@pytest.fixture()
def admin_client(db):
    admin = auth_service.create_user(db, _email(), "adminpass123", is_admin=True)
    token = auth_service.create_session(db, admin)
    c = TestClient(app)
    c.cookies.set("posting_session", token)
    return c


# --- /settings/api-keys ------------------------------------------------------


def test_list_api_keys_as_admin(admin_client):
    resp = admin_client.get("/settings/api-keys")
    assert resp.status_code == 200


def test_list_api_keys_as_non_admin_403(client):
    resp = client.get("/settings/api-keys")
    assert resp.status_code == 403


def test_list_api_keys_unauthenticated_redirects_to_login():
    c = TestClient(app)
    resp = c.get("/settings/api-keys", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_create_api_key_as_admin_sets_flash_cookie(admin_client):
    admin_client.get("/settings/api-keys")
    csrf = admin_client.cookies.get("csrf_token")
    resp = admin_client.post(
        "/settings/api-keys", data={"csrf_token": csrf, "name": "CI Key"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert admin_client.cookies.get("flash_new_api_key") is not None


def test_new_api_key_shown_once_then_cookie_cleared(admin_client):
    admin_client.get("/settings/api-keys")
    csrf = admin_client.cookies.get("csrf_token")
    admin_client.post("/settings/api-keys", data={"csrf_token": csrf, "name": "CI Key"}, follow_redirects=False)
    first_view = admin_client.get("/settings/api-keys")
    assert "CI Key" in first_view.text
    assert admin_client.cookies.get("flash_new_api_key") is None


def test_revoke_api_key_as_admin(admin_client, db):
    from app.services import api_key_service

    admin_client.get("/settings/api-keys")
    csrf = admin_client.cookies.get("csrf_token")
    admin_client.post("/settings/api-keys", data={"csrf_token": csrf, "name": "Revoke Me"}, follow_redirects=False)
    keys = api_key_service.list_api_keys(db)
    target = next(k for k in keys if k.name == "Revoke Me")
    resp = admin_client.post(
        f"/settings/api-keys/{target.id}/revoke", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert resp.status_code == 303
    db.refresh(target)
    assert target.revoked_at is not None


def test_revoke_api_key_nonexistent_404(admin_client):
    admin_client.get("/settings/api-keys")
    csrf = admin_client.cookies.get("csrf_token")
    resp = admin_client.post("/settings/api-keys/999999/revoke", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 404


# --- /settings/users ---------------------------------------------------


def test_list_users_as_admin(admin_client):
    resp = admin_client.get("/settings/users")
    assert resp.status_code == 200


def test_create_user_valid(admin_client):
    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    email = _email()
    resp = admin_client.post(
        "/settings/users",
        data={"csrf_token": csrf, "email": email, "password": "longenough123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]


def test_create_user_short_password_error(admin_client):
    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    resp = admin_client.post(
        "/settings/users",
        data={"csrf_token": csrf, "email": _email(), "password": "short"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_create_user_duplicate_email_error(admin_client, db):
    email = _email()
    auth_service.create_user(db, email, "password123")
    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    resp = admin_client.post(
        "/settings/users",
        data={"csrf_token": csrf, "email": email, "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "already in use" in resp.headers["location"] or "error" in resp.headers["location"]


def test_create_user_email_normalized_case_and_whitespace(admin_client, db):
    from app.services import user_service

    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    admin_client.post(
        "/settings/users",
        data={"csrf_token": csrf, "email": "  MixedCase@Example.com  ", "password": "password123"},
        follow_redirects=False,
    )
    assert user_service.get_user_by_email(db, "mixedcase@example.com") is not None


def test_deactivate_other_user(admin_client, db):
    target = auth_service.create_user(db, _email(), "password123")
    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    resp = admin_client.post(
        f"/settings/users/{target.id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]
    db.refresh(target)
    assert target.is_active is False


def test_deactivate_self_blocked(admin_client, db):
    from app.models.session import Session
    from app.utils.security import sha256_hex

    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    token_hash_matches = (
        db.query(Session).filter(Session.token_hash == sha256_hex(admin_client.cookies.get("posting_session"))).first()
    )
    admin_id = token_hash_matches.user_id
    resp = admin_client.post(
        f"/settings/users/{admin_id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert "cannot%20deactivate%20your%20own%20account" in resp.headers["location"]


def test_deactivate_nonexistent_user_404(admin_client):
    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    resp = admin_client.post("/settings/users/999999/deactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 404


def test_activate_user(admin_client, db):
    from app.services import user_service

    target = auth_service.create_user(db, _email(), "password123")
    user_service.set_active(db, target, False)
    admin_client.get("/settings/users")
    csrf = admin_client.cookies.get("csrf_token")
    resp = admin_client.post(
        f"/settings/users/{target.id}/activate", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert resp.status_code == 303
    db.refresh(target)
    assert target.is_active is True


# --- /settings/password (non-admin allowed) ---------------------------


def test_password_form_non_admin_ok(client):
    resp = client.get("/settings/password")
    assert resp.status_code == 200


def test_change_password_success(client, test_user):
    client.get("/settings/password")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "password123",
            "new_password": "newpassword456",
            "confirm_password": "newpassword456",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]


def test_change_password_wrong_current_401(client):
    client.get("/settings/password")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "wrong-password",
            "new_password": "newpassword456",
            "confirm_password": "newpassword456",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 401


def test_change_password_mismatch_422(client):
    client.get("/settings/password")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "password123",
            "new_password": "newpassword456",
            "confirm_password": "different789",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 422


def test_change_password_too_short_422(client):
    client.get("/settings/password")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/settings/password",
        data={
            "csrf_token": csrf,
            "current_password": "password123",
            "new_password": "short",
            "confirm_password": "short",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 422
