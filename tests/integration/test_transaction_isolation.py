import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.integration.conftest import rollback_session
from ticketing.core.config import Settings
from ticketing.db.session import Database
from ticketing.models.user import User
from ticketing.repositories.user import UserRepository

pytestmark = pytest.mark.integration


async def _exercise_repository_rollback(settings: Settings, email: str) -> None:
    async with rollback_session(settings) as session:
        repository = UserRepository(session)
        repository.add(User(email=email, password_hash="not-a-real-password-hash"))
        await session.flush()
        assert await repository.get_by_email(email) is not None

    database = Database.from_settings(settings)
    try:
        async with database.session() as verification_session:
            stored = await verification_session.scalar(select(User.id).where(User.email == email))
            assert stored is None
    finally:
        await database.dispose()


def test_repository_fixture_rolls_back_without_leaking_rows(
    test_settings: Settings,
) -> None:
    """Single-connection repository tests can be isolated by an outer transaction.

    API and concurrency tests use multiple pooled connections, so their isolation is
    deliberately provided by the per-test test-database cleanup fixture instead.
    """
    asyncio.run(
        _exercise_repository_rollback(
            test_settings,
            f"rollback-{uuid4()}@example.com",
        )
    )
