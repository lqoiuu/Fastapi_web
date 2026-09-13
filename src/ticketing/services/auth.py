from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.core.config import Settings
from ticketing.core.security import (
    PasswordManager,
    TokenClaims,
    TokenCodec,
    TokenType,
    TokenValidationError,
    hash_token,
)
from ticketing.models.refresh_token import RefreshToken
from ticketing.models.user import User
from ticketing.repositories.refresh_token import RefreshTokenRepository
from ticketing.repositories.user import UserRepository


class AuthServiceError(Exception):
    """Base class for expected authentication failures."""


class EmailAlreadyRegisteredError(AuthServiceError):
    pass


class InvalidCredentialsError(AuthServiceError):
    pass


class InvalidTokenError(AuthServiceError):
    pass


class InvalidCurrentPasswordError(AuthServiceError):
    pass


class PasswordUnchangedError(AuthServiceError):
    pass


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    """Coordinate authentication rules and own their database transactions."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        password_manager: PasswordManager,
    ) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)
        self._passwords = password_manager
        self._settings = settings
        self._tokens = TokenCodec(
            settings.auth_secret_key.get_secret_value(),
            access_ttl=timedelta(minutes=settings.access_token_ttl_minutes),
            refresh_ttl=timedelta(days=settings.refresh_token_ttl_days),
        )

    async def register(self, email: str, password: str) -> User:
        normalized_email = self._normalize_email(email)
        if await self._users.get_by_email(normalized_email) is not None:
            raise EmailAlreadyRegisteredError

        user = User(
            email=normalized_email,
            password_hash=self._passwords.hash(password),
        )
        self._users.add(user)

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise EmailAlreadyRegisteredError from exc

        await self._session.refresh(user)
        return user

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self._users.get_by_email(self._normalize_email(email))
        if user is None:
            self._passwords.verify_dummy(password)
            raise InvalidCredentialsError
        if not self._passwords.verify(password, user.password_hash) or not user.is_active:
            raise InvalidCredentialsError

        pair = self._issue_pair(user.id)
        await self._session.commit()
        return pair

    async def refresh(self, raw_token: str) -> TokenPair:
        claims = self._decode_token(raw_token, expected_type="refresh")
        stored_token = await self._refresh_tokens.get_for_update(claims.token_id)
        if (
            stored_token is None
            or stored_token.user_id != claims.subject
            or stored_token.token_hash != hash_token(raw_token)
            or stored_token.expires_at <= datetime.now(UTC)
        ):
            raise InvalidTokenError

        if stored_token.revoked_at is not None:
            if stored_token.replaced_by_id is not None:
                await self._refresh_tokens.revoke_all_active(
                    stored_token.user_id,
                    datetime.now(UTC),
                )
                await self._session.commit()
            raise InvalidTokenError

        user = await self._users.get_by_id(stored_token.user_id)
        if user is None or not user.is_active:
            raise InvalidTokenError

        now = datetime.now(UTC)
        pair = self._issue_pair(user.id)
        replacement_claims = self._decode_token(pair.refresh_token, expected_type="refresh")
        await self._session.flush()
        stored_token.revoked_at = now
        stored_token.replaced_by_id = replacement_claims.token_id
        await self._session.commit()
        return pair

    async def logout(self, raw_token: str) -> None:
        claims = self._decode_token(raw_token, expected_type="refresh")
        stored_token = await self._refresh_tokens.get_for_update(claims.token_id)
        if (
            stored_token is None
            or stored_token.user_id != claims.subject
            or stored_token.token_hash != hash_token(raw_token)
        ):
            raise InvalidTokenError

        if stored_token.revoked_at is None:
            stored_token.revoked_at = datetime.now(UTC)
            await self._session.commit()

    async def authenticate_access_token(self, raw_token: str) -> User:
        claims = self._decode_token(raw_token, expected_type="access")
        user = await self._users.get_by_id(claims.subject)
        if user is None or not user.is_active:
            raise InvalidTokenError
        return user

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        if not self._passwords.verify(current_password, user.password_hash):
            raise InvalidCurrentPasswordError
        if self._passwords.verify(new_password, user.password_hash):
            raise PasswordUnchangedError

        user.password_hash = self._passwords.hash(new_password)
        await self._refresh_tokens.revoke_all_active(user.id, datetime.now(UTC))
        await self._session.commit()

    def _issue_pair(self, user_id: UUID) -> TokenPair:
        access_token = self._tokens.create_access_token(user_id)
        refresh_token = self._tokens.create_refresh_token(user_id)
        self._refresh_tokens.add(
            RefreshToken(
                id=refresh_token.token_id,
                user_id=user_id,
                token_hash=hash_token(refresh_token.value),
                expires_at=refresh_token.expires_at,
            )
        )
        return TokenPair(
            access_token=access_token.value,
            refresh_token=refresh_token.value,
            expires_in=self._settings.access_token_ttl_minutes * 60,
        )

    def _decode_token(self, raw_token: str, *, expected_type: TokenType) -> TokenClaims:
        try:
            return self._tokens.decode(raw_token, expected_type=expected_type)
        except TokenValidationError as exc:
            raise InvalidTokenError from exc

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()
