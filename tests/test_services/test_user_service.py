import itertools

from app.services import auth_service, user_service

_seq = itertools.count()


def _email():
    return f"user{next(_seq)}@example.com"


def test_list_users_ordered_by_email(db):
    auth_service.create_user(db, "zzz@example.com", "password123")
    auth_service.create_user(db, "aaa@example.com", "password123")
    emails = [u.email for u in user_service.list_users(db)]
    assert emails == sorted(emails)


def test_get_user_existing(db):
    user = auth_service.create_user(db, _email(), "password123")
    assert user_service.get_user(db, user.id).id == user.id


def test_get_user_nonexistent_returns_none(db):
    assert user_service.get_user(db, 999999) is None


def test_get_user_by_email_existing(db):
    email = _email()
    user = auth_service.create_user(db, email, "password123")
    assert user_service.get_user_by_email(db, email).id == user.id


def test_get_user_by_email_nonexistent_returns_none(db):
    assert user_service.get_user_by_email(db, "nobody@example.com") is None


def test_set_active_toggle(db):
    user = auth_service.create_user(db, _email(), "password123")
    user_service.set_active(db, user, False)
    assert user.is_active is False
    user_service.set_active(db, user, True)
    assert user.is_active is True
