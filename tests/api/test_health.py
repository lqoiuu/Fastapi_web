from contextlib import AbstractAsyncContextManager

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ticketing.core.config import Settings, get_settings
from ticketing.db.session import DatabaseUnavailableError
from ticketing.main import create_app


class FakeDatabase:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.ping_count = 0
        self.disposed = False

    def session(self) -> AbstractAsyncContextManager[AsyncSession]:
        raise AssertionError("A health endpoint must not request a database session")

    async def ping(self) -> None:
        self.ping_count += 1
        if not self.available:
            raise DatabaseUnavailableError("Database unavailable during test")

    async def dispose(self) -> None:
        self.disposed = True


def create_test_client(
    database: FakeDatabase | None = None,
) -> tuple[TestClient, FakeDatabase]:
    settings = Settings(environment="test")
    test_database = database or FakeDatabase()
    app = create_app(settings, database=test_database)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), test_database


def test_liveness_reports_process_health() -> None:
    client, _ = create_test_client()

    with client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "FastAPI Ticketing System",
        "environment": "test",
    }


def test_readiness_checks_database_after_startup() -> None:
    client, database = create_test_client()

    with client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert database.ping_count == 1
    assert database.disposed is True


def test_readiness_rejects_requests_before_lifespan_startup() -> None:
    client, database = create_test_client()

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "service_not_ready"
    assert database.ping_count == 0


def test_readiness_reports_database_failure_without_leaking_details() -> None:
    client, _ = create_test_client(FakeDatabase(available=False))

    with client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"] == {
        "code": "database_unavailable",
        "message": "Database is unavailable",
    }
    assert payload["correlation_id"] == response.headers["X-Correlation-ID"]


def test_versioned_router_is_registered() -> None:
    client, _ = create_test_client()

    with client:
        response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.json() == {"api_version": "v1"}
