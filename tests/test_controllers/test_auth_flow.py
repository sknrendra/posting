import itertools

from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.main import app
from app.services import auth_service

_seq = itertools.count()


def _email():
    return f"authflow{next(_seq)}@example.com"


def test_login_form(db):
    c = TestClient(app)
    resp = c.get("/login")
    assert resp.status_code == 200


def test_login_success_sets_session_cookie(db):
    email = _email()
    auth_service.create_user(db, email, "password123")
    c = TestClient(app)
    c.get("/login")
    csrf = c.cookies.get("csrf_token")
    resp = c.post(
        "/login", data={"csrf_token": csrf, "email": email, "password": "password123"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert c.cookies.get(app_settings.session_cookie_name) is not None


def test_login_wrong_password_returns_401(db):
    email = _email()
    auth_service.create_user(db, email, "password123")
    c = TestClient(app)
    c.get("/login")
    csrf = c.cookies.get("csrf_token")
    resp = c.post(
        "/login", data={"csrf_token": csrf, "email": email, "password": "wrong"}, follow_redirects=False
    )
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text
    assert email in resp.text  # email echoed back
    assert "wrong" not in resp.text  # password not echoed


def test_login_nonexistent_email_returns_same_401_message(db):
    c = TestClient(app)
    c.get("/login")
    csrf = c.cookies.get("csrf_token")
    resp = c.post(
        "/login",
        data={"csrf_token": csrf, "email": "nobody@example.com", "password": "whatever"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text


def test_login_deactivated_user_returns_same_401(db):
    from app.services import user_service

    email = _email()
    user = auth_service.create_user(db, email, "password123")
    user_service.set_active(db, user, False)
    c = TestClient(app)
    c.get("/login")
    csrf = c.cookies.get("csrf_token")
    resp = c.post(
        "/login", data={"csrf_token": csrf, "email": email, "password": "password123"}, follow_redirects=False
    )
    assert resp.status_code == 401


def test_login_missing_csrf_returns_403(db):
    email = _email()
    auth_service.create_user(db, email, "password123")
    c = TestClient(app)
    resp = c.post(
        "/login", data={"csrf_token": "bogus", "email": email, "password": "password123"}, follow_redirects=False
    )
    assert resp.status_code == 403


def test_logout_destroys_session(client, test_user, db):
    resp = client.get("/")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    # The session token that was in use no longer authenticates.
    old_session_cookie = client.cookies.get(app_settings.session_cookie_name)
    protected = client.get("/accounts", follow_redirects=False)
    assert protected.status_code == 303
    assert protected.headers["location"] == "/login"


def test_logout_without_session_cookie_no_crash(db):
    c = TestClient(app)
    c.get("/login")
    csrf = c.cookies.get("csrf_token")
    resp = c.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
