from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from time import perf_counter
from typing import Any, Protocol, Self

from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ticketing.core.config import Settings
from ticketing.core.observability import DatabaseMetricsRecorder


class DatabaseUnavailableError(RuntimeError):
    """Raised when the database cannot answer a lightweight health query."""


class DatabaseProtocol(Protocol):
    """Narrow application-facing database contract used for testing and dependency injection."""

    def session(self) -> AbstractAsyncContextManager[AsyncSession]: ...

    async def ping(self) -> None: ...

    async def dispose(self) -> None: ...


class Database:
    """Own the async engine and create isolated sessions for individual requests."""

    def __init__(
        self,
        url: str,
        *,
        pool_size: int,
        max_overflow: int,
        pool_timeout_seconds: float,
        metrics: DatabaseMetricsRecorder | None = None,
    ) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_seconds,
        )
        self._session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        if metrics is not None:
            self._install_query_metrics(metrics)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        metrics: DatabaseMetricsRecorder | None = None,
    ) -> Self:
        return cls(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout_seconds=settings.database_pool_timeout_seconds,
            metrics=metrics,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            yield session

    async def ping(self) -> None:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (OSError, SQLAlchemyError) as exc:
            raise DatabaseUnavailableError("Database health query failed") from exc

    async def dispose(self) -> None:
        await self.engine.dispose()

    def _install_query_metrics(self, metrics: DatabaseMetricsRecorder) -> None:
        def before_cursor_execute(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            del cursor, parameters, context, executemany
            connection.info["ticketing_query_started_at"] = perf_counter()
            connection.info["ticketing_query_operation"] = self._query_operation(statement)

        def after_cursor_execute(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            del cursor, statement, parameters, context, executemany
            started_at = connection.info.pop("ticketing_query_started_at", None)
            operation = connection.info.pop("ticketing_query_operation", "OTHER")
            if isinstance(started_at, float):
                metrics.observe_database_query(str(operation), perf_counter() - started_at)

        event.listen(self.engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        event.listen(self.engine.sync_engine, "after_cursor_execute", after_cursor_execute)

    @staticmethod
    def _query_operation(statement: str) -> str:
        operation = statement.lstrip().partition(" ")[0].upper()
        return operation if operation in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "OTHER"
