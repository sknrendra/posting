import itertools
from datetime import timedelta

from app.models.base import utcnow
from app.models.session import Session
from app.services import auth_service

_seq = itertools.count()


def _email():
    return f"user{next(_seq)}@example.com"


# --- authenticate --------------------------------------------------------


def test_authenticate_correct_credentials(db):
    email = _email()
    user = auth_service.create_user(db, email, "password123")
    result = auth_service.authenticate(db, email, "password123")
    assert result.id == user.id


def test_authenticate_wrong_password_returns_none(db):
    email = _email()
    auth_service.create_user(db, email, "password123")
    assert auth_service.authenticate(db, email, "wrong-password") is None


def test_authenticate_nonexistent_email_returns_none(db):
    assert auth_service.authenticate(db, "nobody@example.com", "whatever") is None


def test_authenticate_inactive_user_returns_none(db):
    from app.services import user_service

    email = _email()
    user = auth_service.create_user(db, email, "password123")
    user_service.set_active(db, user, False)
    assert auth_service.authenticate(db, email, "password123") is None


# --- create_session / get_user_for_session_token ---------------------------


def test_create_session_and_lookup(db):
    user = auth_service.create_user(db, _email(), "password123")
    token = auth_service.create_session(db, user)
    session = db.query(Session).filter(Session.user_id == user.id).first()
    assert session is not None
    assert session.token_hash != token  # stored hashed, not plaintext


def test_get_user_for_session_token_valid_slides_expiration(db):
    user = auth_service.create_user(db, _email(), "password123")
    token = auth_service.create_session(db, user)
    session = db.query(Session).filter(Session.user_id == user.id).first()
    original_expires_at = session.expires_at

    session.expires_at = utcnow() + timedelta(days=1)
    db.commit()

    found = auth_service.get_user_for_session_token(db, token)
    assert found.id == user.id
    db.refresh(session)
    assert session.expires_at > original_expires_at - timedelta(days=1)


def test_get_user_for_session_token_unknown_token_returns_none(db):
    assert auth_service.get_user_for_session_token(db, "not-a-real-token") is None


def test_get_user_for_session_token_expired_returns_none_and_does_not_slide(db):
    user = auth_service.create_user(db, _email(), "password123")
    token = auth_service.create_session(db, user)
    session = db.query(Session).filter(Session.user_id == user.id).first()
    session.expires_at = utcnow() - timedelta(days=1)
    db.commit()
    expired_at = session.expires_at

    assert auth_service.get_user_for_session_token(db, token) is None
    db.refresh(session)
    assert session.expires_at == expired_at


def test_get_user_for_session_token_deactivated_user_returns_none(db):
    from app.services import user_service

    user = auth_service.create_user(db, _email(), "password123")
    token = auth_service.create_session(db, user)
    user_service.set_active(db, user, False)
    assert auth_service.get_user_for_session_token(db, token) is None


def test_get_user_for_session_token_orphaned_session_returns_none(db):
    """sessions.user_id FKs to users, so a user can't normally be deleted
    while a session still references it — simulate the orphaned-row case
    (e.g. a bulk data-repair script) by disabling FK enforcement for one
    delete, to confirm the service's `if not user` guard handles it rather
    than crashing on `user.is_active` against None."""
    from sqlalchemy import text

    user = auth_service.create_user(db, _email(), "password123")
    token = auth_service.create_session(db, user)
    db.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
        db.commit()
        assert auth_service.get_user_for_session_token(db, token) is None
    finally:
        db.execute(text("PRAGMA foreign_keys=ON"))


# --- destroy_session ------------------------------------------------------


def test_destroy_session_removes_it(db):
    user = auth_service.create_user(db, _email(), "password123")
    token = auth_service.create_session(db, user)
    auth_service.destroy_session(db, token)
    assert auth_service.get_user_for_session_token(db, token) is None


def test_destroy_session_unknown_token_is_noop(db):
    auth_service.destroy_session(db, "never-existed")  # must not raise


# --- create_user -----------------------------------------------------------


def test_create_user_defaults(db):
    user = auth_service.create_user(db, _email(), "password123")
    assert user.is_active is True
    assert user.is_admin is False
    assert user.password_hash != "password123"


def test_create_user_admin_flag(db):
    user = auth_service.create_user(db, _email(), "password123", is_admin=True)
    assert user.is_admin is True


# --- set_password ------------------------------------------------------


def test_set_password_changes_hash_and_auth_behavior(db):
    email = _email()
    user = auth_service.create_user(db, email, "old-password")
    auth_service.set_password(db, user, "new-password")
    assert auth_service.authenticate(db, email, "old-password") is None
    assert auth_service.authenticate(db, email, "new-password").id == user.id
