from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import ticketing.models  # noqa: F401
from tests.support.api_factory import (
    TEST_ATTACHMENT_PATH,
    TEST_EXPORT_PATH,
    assert_safe_test_resources,
    build_test_settings,
)
from ticketing.core.config import Settings
from ticketing.db.base import Base
from ticketing.db.session import Database

MIGRATION_SEEDED_TABLES = {"permissions"}


async def _reset_database(settings: Settings) -> None:
    assert_safe_test_resources(settings)
    resettable_tables = (
        table
        for table in reversed(Base.metadata.sorted_tables)
        if table.name not in MIGRATION_SEEDED_TABLES
    )
    table_names = ", ".join(f'"{table.name}"' for table in resettable_tables)
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            await session.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
            await session.commit()
    finally:
        await database.dispose()


async def _reset_redis(settings: Settings) -> None:
    assert_safe_test_resources(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


@pytest.fixture(autouse=True)
def isolated_integration_state() -> Iterator[None]:
    """Keep API tests independent without ever touching development resources."""
    settings = build_test_settings()
    asyncio.run(_reset_database(settings))
    asyncio.run(_reset_redis(settings))
    shutil.rmtree(TEST_ATTACHMENT_PATH, ignore_errors=True)
    shutil.rmtree(TEST_EXPORT_PATH, ignore_errors=True)
    yield
    asyncio.run(_reset_database(settings))
    asyncio.run(_reset_redis(settings))
    shutil.rmtree(TEST_ATTACHMENT_PATH, ignore_errors=True)
    shutil.rmtree(TEST_EXPORT_PATH, ignore_errors=True)


@asynccontextmanager
async def rollback_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    """Yield one repository session and roll its outer transaction back."""
    database = Database.from_settings(settings)
    connection = await database.engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await database.dispose()


@pytest.fixture
def test_settings() -> Settings:
    settings = build_test_settings()
    assert_safe_test_resources(settings)
    return settings
