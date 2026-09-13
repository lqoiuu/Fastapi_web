from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError as PyJWTInvalidTokenError
from pwdlib import PasswordHash

TokenType = Literal["access", "refresh"]


class TokenValidationError(ValueError):
    """Raised when a JWT is invalid, expired, or has unexpected claims."""


@dataclass(frozen=True, slots=True)
class EncodedToken:
    value: str
    token_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: UUID
    token_id: UUID
    token_type: TokenType
    expires_at: datetime


class PasswordManager:
    """Hash and verify passwords using pwdlib's current recommended algorithm."""

    def __init__(self) -> None:
        self._hasher = PasswordHash.recommended()
        self._dummy_hash = self._hasher.hash("not-a-real-user-password")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        return self._hasher.verify(password, password_hash)

    def verify_dummy(self, password: str) -> None:
        """Perform comparable work when an account does not exist."""
        self._hasher.verify(password, self._dummy_hash)


class TokenCodec:
    """Issue and validate signed access and refresh JWTs."""

    def __init__(
        self,
        secret_key: str,
        *,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
        algorithm: str = "HS256",
    ) -> None:
        self._secret_key = secret_key
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl
        self._algorithm = algorithm

    def create_access_token(self, user_id: UUID) -> EncodedToken:
        return self._encode(user_id, "access", self._access_ttl)

    def create_refresh_token(self, user_id: UUID) -> EncodedToken:
        return self._encode(user_id, "refresh", self._refresh_ttl)

    def decode(self, token: str, *, expected_type: TokenType) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                options={"require": ["sub", "jti", "type", "iat", "exp"]},
            )
            token_type = payload["type"]
            if token_type != expected_type:
                raise TokenValidationError("Unexpected token type")
            return TokenClaims(
                subject=UUID(payload["sub"]),
                token_id=UUID(payload["jti"]),
                token_type=token_type,
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            )
        except (KeyError, TypeError, ValueError, PyJWTInvalidTokenError) as exc:
            if isinstance(exc, TokenValidationError):
                raise
            raise TokenValidationError("Invalid token") from exc

    def _encode(self, user_id: UUID, token_type: TokenType, ttl: timedelta) -> EncodedToken:
        now = datetime.now(UTC)
        expires_at = now + ttl
        token_id = uuid4()
        value = jwt.encode(
            {
                "sub": str(user_id),
                "jti": str(token_id),
                "type": token_type,
                "iat": now,
                "exp": expires_at,
            },
            self._secret_key,
            algorithm=self._algorithm,
        )
        return EncodedToken(value=value, token_id=token_id, expires_at=expires_at)


def hash_token(token: str) -> str:
    """Create the non-reversible representation stored for refresh tokens."""
    return sha256(token.encode("utf-8")).hexdigest()
