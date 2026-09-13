from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.cache.backend import CacheMetrics, RateLimitDecision
from ticketing.core.config import Settings, get_settings
from ticketing.db.session import DatabaseProtocol
from ticketing.main import create_app


class FakeDatabase:
    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, object())

    async def ping(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class FakeCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        return RateLimitDecision(True, limit, limit, 0)

    async def ping(self) -> None:
        return None

    def metrics_snapshot(self) -> CacheMetrics:
        return CacheMetrics(0, 0, 0, 0, 0, 0)

    async def close(self) -> None:
        return None


def build_app() -> FastAPI:
    settings = Settings(
        environment="test",
        auth_secret_key=SecretStr("observability-test-secret-at-least-32-characters"),
    )
    database: DatabaseProtocol = FakeDatabase()
    app = create_app(settings, database=database, cache=FakeCache())
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def test_correlation_id_is_accepted_or_safely_replaced() -> None:
    app = build_app()
    with TestClient(app) as client:
        accepted = client.get(
            "/health/live",
            headers={"X-Correlation-ID": "client-correlation-123"},
        )
        replaced = client.get(
            "/health/live",
            headers={"X-Correlation-ID": "bad"},
        )

    assert accepted.headers["X-Correlation-ID"] == "client-correlation-123"
    assert replaced.headers["X-Correlation-ID"] != "bad"
    assert len(replaced.headers["X-Correlation-ID"]) == 32


def test_validation_error_is_uniform_and_does_not_echo_password() -> None:
    app = build_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"X-Correlation-ID": "validation-correlation-123"},
            json={"email": "not-an-email", "password": "plaintext-password"},
        )

    assert response.status_code == 422
    assert response.headers["X-Correlation-ID"] == "validation-correlation-123"
    payload = response.json()
    assert payload["correlation_id"] == "validation-correlation-123"
    assert payload["detail"]["code"] == "request_validation_error"
    assert "plaintext-password" not in response.text


def test_unhandled_error_is_generic_and_correlated() -> None:
    app = build_app()

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("private@example.com secret internal failure")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/explode",
            headers={"X-Correlation-ID": "failure-correlation-123"},
        )

    assert response.status_code == 500
    assert response.headers["X-Correlation-ID"] == "failure-correlation-123"
    assert response.json() == {
        "detail": {"code": "internal_server_error", "message": "Internal server error"},
        "correlation_id": "failure-correlation-123",
    }
    assert "private@example.com" not in response.text
