from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from ticketing.cache.backend import CacheBackend
from ticketing.core.config import Settings, get_settings
from ticketing.main import create_app

TEST_PASSWORD = "correct horse battery staple"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_ATTACHMENT_PATH = PROJECT_ROOT / ".pytest_attachment_storage"
TEST_EXPORT_PATH = PROJECT_ROOT / ".pytest_export_storage"

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://ticketing:ticketing@127.0.0.1:5432/ticketing_test"
DEFAULT_TEST_REDIS_URL = "redis://127.0.0.1:6379/15"
DEFAULT_TEST_CELERY_BROKER_URL = "redis://127.0.0.1:6379/14"


def build_test_settings(**overrides: Any) -> Settings:
    """Build settings that default to resources reserved for automated tests."""
    values: dict[str, Any] = {
        "environment": "test",
        "auth_secret_key": "integration-test-secret-key-at-least-32-characters",
        "database_url": os.getenv("TICKETING_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL),
        "redis_url": os.getenv("TICKETING_TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL),
        "celery_broker_url": os.getenv(
            "TICKETING_TEST_CELERY_BROKER_URL", DEFAULT_TEST_CELERY_BROKER_URL
        ),
        "attachment_storage_path": TEST_ATTACHMENT_PATH,
        "export_storage_path": TEST_EXPORT_PATH,
    }
    values.update(overrides)
    return Settings(**values)


def assert_safe_test_resources(settings: Settings) -> None:
    """Fail closed before a destructive test reset can reach shared resources."""
    if settings.environment != "test":
        raise RuntimeError("Integration cleanup requires environment='test'")

    database_name = make_url(settings.database_url).database or ""
    if not database_name.lower().endswith("_test"):
        raise RuntimeError(
            "Integration tests refuse to clean a database whose name does not end in '_test'"
        )

    redis_path = urlsplit(settings.redis_url).path.removeprefix("/")
    try:
        redis_database = int(redis_path)
    except ValueError as exc:
        raise RuntimeError("Integration tests require a numbered Redis database") from exc
    if redis_database in {0, 1}:
        raise RuntimeError("Integration tests refuse to flush Redis database 0 or 1")


def create_client(
    cache: CacheBackend | None = None,
    **settings_overrides: Any,
) -> TestClient:
    settings = build_test_settings(**settings_overrides)
    app = create_app(settings, cache=cache)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def register(
    client: TestClient,
    email: str = "user@example.com",
    *,
    password: str = TEST_PASSWORD,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def login(
    client: TestClient,
    *,
    email: str = "user@example.com",
    password: str = TEST_PASSWORD,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


def bearer(token: object) -> dict[str, str]:
    assert isinstance(token, str)
    return {"Authorization": f"Bearer {token}"}


def register_and_login_tokens(client: TestClient, email: str) -> dict[str, object]:
    register(client, email)
    return login(client, email=email)


def auth_headers(tokens: dict[str, object]) -> dict[str, str]:
    return bearer(tokens["access_token"])


def register_and_login(client: TestClient, email: str) -> dict[str, str]:
    return auth_headers(register_and_login_tokens(client, email))


def with_tenant(headers: dict[str, str], tenant_id: str) -> dict[str, str]:
    return {**headers, "X-Tenant-ID": tenant_id}


def ticket_headers(
    headers: dict[str, str],
    tenant_id: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    return {
        **with_tenant(headers, tenant_id),
        "Idempotency-Key": idempotency_key or str(uuid4()),
    }


def create_tenant(
    client: TestClient,
    headers: dict[str, str],
    slug: str | None = None,
    *,
    name: str | None = None,
) -> str:
    assert slug is not None
    response = client.post(
        "/api/v1/tenants",
        headers=headers,
        json={"name": name or f"Tenant {slug}", "slug": slug},
    )
    assert response.status_code == 201, response.text
    tenant_id = response.json()["tenant"]["id"]
    assert isinstance(tenant_id, str)
    return tenant_id


def add_member(
    client: TestClient,
    *,
    owner: dict[str, str],
    tenant_id: str,
    member: dict[str, str],
    email: str,
) -> str:
    invitation = client.post(
        f"/api/v1/tenants/{tenant_id}/invitations",
        headers=with_tenant(owner, tenant_id),
        json={"email": email},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        f"/api/v1/tenants/{tenant_id}/membership/accept",
        headers=member,
    )
    assert accepted.status_code == 200, accepted.text
    membership_id = invitation.json()["id"]
    assert isinstance(membership_id, str)
    return membership_id


def role_ids(
    client: TestClient,
    owner: dict[str, str],
    tenant_id: str,
) -> dict[str, str]:
    response = client.get(
        f"/api/v1/tenants/{tenant_id}/roles",
        headers=with_tenant(owner, tenant_id),
    )
    assert response.status_code == 200, response.text
    return {item["name"]: item["id"] for item in response.json()}


def assign_role(
    client: TestClient,
    *,
    owner: dict[str, str],
    tenant_id: str,
    membership_id: str,
    role_id: str,
) -> None:
    response = client.put(
        f"/api/v1/tenants/{tenant_id}/members/{membership_id}/roles",
        headers=with_tenant(owner, tenant_id),
        json={"role_ids": [role_id]},
    )
    assert response.status_code == 204, response.text


def create_ticket_with_customer(
    client: TestClient,
    headers: dict[str, str],
    tenant_id: str,
    *,
    subject: str,
) -> str:
    customer = client.post(
        f"/api/v1/tenants/{tenant_id}/customers",
        headers=with_tenant(headers, tenant_id),
        json={"name": "Background Customer", "email": "customer@example.com"},
    )
    assert customer.status_code == 201, customer.text
    ticket = client.post(
        f"/api/v1/tenants/{tenant_id}/tickets",
        headers=ticket_headers(headers, tenant_id, f"ticket-{uuid4()}"),
        json={
            "customer_id": customer.json()["id"],
            "subject": subject,
            "description": "Created for a durable background job test",
        },
    )
    assert ticket.status_code == 201, ticket.text
    ticket_id = ticket.json()["id"]
    assert isinstance(ticket_id, str)
    return ticket_id
