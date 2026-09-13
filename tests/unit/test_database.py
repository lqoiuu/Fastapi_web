import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.core.config import Settings
from ticketing.db.base import TimestampMixin, UUIDPrimaryKeyMixin
from ticketing.db.session import Database


def create_database() -> Database:
    settings = Settings(environment="test")
    return Database.from_settings(settings)


def test_database_uses_async_postgresql_driver() -> None:
    database = create_database()

    assert database.engine.url.drivername == "postgresql+asyncpg"

    asyncio.run(database.dispose())


def test_session_context_creates_async_session_without_connecting() -> None:
    database = create_database()

    async def inspect_session() -> None:
        async with database.session() as session:
            assert isinstance(session, AsyncSession)
        await database.dispose()

    asyncio.run(inspect_session())


def test_model_mixins_declare_standard_columns() -> None:
    assert "id" in UUIDPrimaryKeyMixin.__annotations__
    assert set(TimestampMixin.__annotations__) == {"created_at", "updated_at"}
