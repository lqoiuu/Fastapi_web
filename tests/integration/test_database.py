import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.pool import QueuePool

from ticketing.core.config import Settings
from ticketing.db.session import Database


@pytest.mark.integration
def test_session_executes_query_and_returns_connection_to_pool(
    test_settings: Settings,
) -> None:
    database = Database.from_settings(test_settings)

    async def exercise_session() -> None:
        pool = database.engine.pool
        assert isinstance(pool, QueuePool)

        async with database.session() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
            assert pool.checkedout() == 1

        assert pool.checkedout() == 0
        await database.dispose()

    asyncio.run(exercise_session())
