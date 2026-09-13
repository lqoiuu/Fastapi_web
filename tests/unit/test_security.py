from datetime import timedelta
from uuid import uuid4

import pytest

from ticketing.core.security import (
    PasswordManager,
    TokenCodec,
    TokenValidationError,
    hash_token,
)

SECRET_KEY = "unit-test-secret-key-with-at-least-32-characters"


def create_codec(
    *,
    access_ttl: timedelta = timedelta(minutes=15),
    refresh_ttl: timedelta = timedelta(days=7),
) -> TokenCodec:
    return TokenCodec(
        SECRET_KEY,
        access_ttl=access_ttl,
        refresh_ttl=refresh_ttl,
    )


def test_password_is_hashed_and_verifiable() -> None:
    manager = PasswordManager()

    password_hash = manager.hash("correct horse battery staple")

    assert password_hash != "correct horse battery staple"
    assert password_hash.startswith("$argon2")
    assert manager.verify("correct horse battery staple", password_hash) is True
    assert manager.verify("wrong password", password_hash) is False


def test_access_token_round_trip_preserves_identity() -> None:
    codec = create_codec()
    user_id = uuid4()

    encoded = codec.create_access_token(user_id)
    claims = codec.decode(encoded.value, expected_type="access")

    assert claims.subject == user_id
    assert claims.token_id == encoded.token_id
    assert claims.token_type == "access"


def test_expired_token_is_rejected() -> None:
    codec = create_codec(access_ttl=timedelta(seconds=-1))
    encoded = codec.create_access_token(uuid4())

    with pytest.raises(TokenValidationError):
        codec.decode(encoded.value, expected_type="access")


def test_tampered_token_is_rejected() -> None:
    codec = create_codec()
    encoded = codec.create_access_token(uuid4())
    header, payload, signature = encoded.value.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = ".".join((header, payload, replacement + signature[1:]))

    with pytest.raises(TokenValidationError):
        codec.decode(tampered, expected_type="access")


def test_refresh_token_cannot_be_used_as_access_token() -> None:
    codec = create_codec()
    encoded = codec.create_refresh_token(uuid4())

    with pytest.raises(TokenValidationError):
        codec.decode(encoded.value, expected_type="access")


def test_refresh_token_hash_is_deterministic_and_non_reversible() -> None:
    raw_token = create_codec().create_refresh_token(uuid4()).value

    digest = hash_token(raw_token)

    assert digest == hash_token(raw_token)
    assert digest != raw_token
    assert len(digest) == 64
