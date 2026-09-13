from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.db.session import DatabaseProtocol


def get_database(request: Request) -> DatabaseProtocol:
    """Return the application-owned database manager."""
    return cast(DatabaseProtocol, request.app.state.database)


async def get_session(
    database: Annotated[DatabaseProtocol, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """Provide one AsyncSession per request without owning the transaction outcome."""
    async with database.session() as session:
        yield session
