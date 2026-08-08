from app.utils.security import (
    constant_time_eq,
    generate_api_key,
    generate_token,
    hash_password,
    sha256_hex,
    verify_password,
)


def test_hash_and_verify_password_round_trip():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", hashed) is True


def test_verify_password_wrong_password_returns_false():
    hashed = hash_password("the-real-password")
    assert verify_password("wrong-password", hashed) is False


def test_generate_token_unique_across_calls():
    assert generate_token() != generate_token()


def test_sha256_hex_deterministic():
    assert sha256_hex("hello") == sha256_hex("hello")
    assert sha256_hex("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_generate_api_key_shape():
    token, prefix, key_hash = generate_api_key()
    assert token.startswith("pk_")
    assert prefix == token[:12]
    assert key_hash == sha256_hex(token)


def test_constant_time_eq_equal_strings():
    assert constant_time_eq("abc123", "abc123") is True


def test_constant_time_eq_different_strings_same_length():
    assert constant_time_eq("abc123", "xyz789") is False


def test_constant_time_eq_different_lengths():
    assert constant_time_eq("short", "a-much-longer-string") is False
