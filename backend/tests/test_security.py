from datetime import timedelta

import pytest
from jose import jwt

from app.core import security
from app.core.config import settings


def test_hash_password_returns_different_value_than_plain():
    password = "super-secret-password"
    hashed = security.hash_password(password)

    assert hashed != password
    assert isinstance(hashed, str)


def test_hash_password_generates_unique_salts():
    password = "super-secret-password"
    hashed_one = security.hash_password(password)
    hashed_two = security.hash_password(password)

    assert hashed_one != hashed_two


def test_verify_password_success():
    password = "correct-horse-battery-staple"
    hashed = security.hash_password(password)

    assert security.verify_password(password, hashed) is True


def test_verify_password_failure():
    password = "correct-horse-battery-staple"
    hashed = security.hash_password(password)

    assert security.verify_password("wrong-password", hashed) is False


def test_create_access_token_contains_expected_claims():
    token = security.create_access_token("user-123")
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"
    assert "iat" in payload
    assert "exp" in payload


def test_create_refresh_token_contains_expected_claims():
    token = security.create_refresh_token("user-123")
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    assert payload["sub"] == "user-123"
    assert payload["type"] == "refresh"
    assert "iat" in payload
    assert "exp" in payload


def test_access_and_refresh_tokens_are_different():
    access_token = security.create_access_token("user-123")
    refresh_token = security.create_refresh_token("user-123")

    assert access_token != refresh_token


def test_decode_token_valid_access_token():
    token = security.create_access_token("user-123")
    payload = security.decode_token(token)

    assert payload is not None
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_decode_token_valid_refresh_token():
    token = security.create_refresh_token("user-456")
    payload = security.decode_token(token)

    assert payload is not None
    assert payload["sub"] == "user-456"
    assert payload["type"] == "refresh"


def test_decode_token_invalid_token_returns_none():
    assert security.decode_token("not-a-valid-token") is None


def test_decode_token_expired_token_returns_none():
    expired_token = security._create_token("user-123", timedelta(minutes=-1), "access")

    assert security.decode_token(expired_token) is None


def test_decode_token_wrong_secret_returns_none():
    token = jwt.encode(
        {"sub": "user-123", "type": "access"},
        "a-completely-different-secret",
        algorithm=settings.JWT_ALGORITHM,
    )

    assert security.decode_token(token) is None
