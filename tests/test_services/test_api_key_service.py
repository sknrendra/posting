from app.services import api_key_service
from app.utils.security import sha256_hex


def test_create_api_key_shape(db, test_user):
    api_key, token = api_key_service.create_api_key(db, "My Key", test_user.id)
    assert api_key.key_hash == sha256_hex(token)
    assert api_key.key_prefix == token[:12]
    assert api_key.revoked_at is None
    assert api_key.created_by_user_id == test_user.id


def test_list_api_keys_ordered_by_created_at_desc(db, test_user):
    first, _ = api_key_service.create_api_key(db, "First", test_user.id)
    second, _ = api_key_service.create_api_key(db, "Second", test_user.id)
    keys = api_key_service.list_api_keys(db)
    ids = [k.id for k in keys]
    assert ids.index(second.id) < ids.index(first.id)


def test_get_api_key_existing(db, test_user):
    api_key, _token = api_key_service.create_api_key(db, "X", test_user.id)
    assert api_key_service.get_api_key(db, api_key.id).id == api_key.id


def test_get_api_key_nonexistent_returns_none(db):
    assert api_key_service.get_api_key(db, 999999) is None


def test_revoke_sets_revoked_at(db, test_user):
    api_key, token = api_key_service.create_api_key(db, "X", test_user.id)
    api_key_service.revoke(db, api_key)
    assert api_key.revoked_at is not None
    assert api_key_service.verify_token(db, token) is None


def test_verify_token_valid_bumps_last_used_at(db, test_user):
    api_key, token = api_key_service.create_api_key(db, "X", test_user.id)
    assert api_key.last_used_at is None
    found = api_key_service.verify_token(db, token)
    assert found.id == api_key.id
    assert found.last_used_at is not None


def test_verify_token_unknown_returns_none(db):
    assert api_key_service.verify_token(db, "pk_not-a-real-token") is None


def test_verify_token_revoked_returns_none(db, test_user):
    api_key, token = api_key_service.create_api_key(db, "X", test_user.id)
    api_key_service.revoke(db, api_key)
    assert api_key_service.verify_token(db, token) is None


def test_verify_token_empty_string_returns_none(db):
    assert api_key_service.verify_token(db, "") is None
