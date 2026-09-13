import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.support.api_factory import (
    TEST_PASSWORD,
    bearer,
    build_test_settings,
    create_client,
    login,
    register,
)
from ticketing.db.session import Database
from ticketing.models.user import User

pytestmark = pytest.mark.integration

NEW_PASSWORD = "a completely different password"


async def stored_password_hash(email: str) -> str:
    database = Database.from_settings(build_test_settings())
    async with database.session() as session:
        result = await session.execute(select(User.password_hash).where(User.email == email))
        password_hash = result.scalar_one()
    await database.dispose()
    return password_hash


def test_registration_login_profile_and_password_change() -> None:
    email = f"user-{uuid4()}@example.com"
    with create_client() as client:
        user = register(client, email.upper())
        assert user["email"] == email
        assert "password" not in user
        assert "password_hash" not in user

        duplicate = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": TEST_PASSWORD},
        )
        assert duplicate.status_code == 409

        missing_user = client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "wrong password"},
        )
        wrong_password = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "wrong password"},
        )
        assert missing_user.status_code == wrong_password.status_code == 401
        assert missing_user.json()["detail"] == wrong_password.json()["detail"]
        assert missing_user.json()["correlation_id"] == missing_user.headers["X-Correlation-ID"]
        assert wrong_password.json()["correlation_id"] == wrong_password.headers["X-Correlation-ID"]
        assert missing_user.json()["correlation_id"] != wrong_password.json()["correlation_id"]

        tokens = login(client, email=email)
        anonymous_profile = client.get("/api/v1/users/me")
        assert anonymous_profile.status_code == 401

        profile = client.get("/api/v1/users/me", headers=bearer(tokens["access_token"]))
        assert profile.status_code == 200
        assert profile.json()["id"] == user["id"]

        wrong_current_password = client.put(
            "/api/v1/users/me/password",
            headers=bearer(tokens["access_token"]),
            json={"current_password": "wrong password", "new_password": NEW_PASSWORD},
        )
        assert wrong_current_password.status_code == 400

        unchanged_password = client.put(
            "/api/v1/users/me/password",
            headers=bearer(tokens["access_token"]),
            json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
        )
        assert unchanged_password.status_code == 400

        changed = client.put(
            "/api/v1/users/me/password",
            headers=bearer(tokens["access_token"]),
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 204

        revoked_refresh = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert revoked_refresh.status_code == 401

        old_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": TEST_PASSWORD},
        )
        assert old_login.status_code == 401
        login(client, email=email, password=NEW_PASSWORD)

    password_hash = asyncio.run(stored_password_hash(email))
    assert password_hash != NEW_PASSWORD
    assert password_hash.startswith("$argon2")


def test_refresh_rotation_detects_reuse_and_revokes_replacement() -> None:
    email = f"refresh-{uuid4()}@example.com"
    with create_client() as client:
        register(client, email)
        original = login(client, email=email)

        rotated_response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )
        assert rotated_response.status_code == 200
        rotated = rotated_response.json()
        assert rotated["refresh_token"] != original["refresh_token"]

        replay = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )
        assert replay.status_code == 401

        replacement_after_replay = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": rotated["refresh_token"]},
        )
        assert replacement_after_replay.status_code == 401


def test_logout_revokes_refresh_token_and_is_idempotent() -> None:
    email = f"logout-{uuid4()}@example.com"
    with create_client() as client:
        register(client, email)
        tokens = login(client, email=email)
        payload = {"refresh_token": tokens["refresh_token"]}

        first_logout = client.post("/api/v1/auth/logout", json=payload)
        second_logout = client.post("/api/v1/auth/logout", json=payload)
        refresh_after_logout = client.post("/api/v1/auth/refresh", json=payload)

        assert first_logout.status_code == 204
        assert second_logout.status_code == 204
        assert refresh_after_logout.status_code == 401


def test_login_rate_limit_is_atomic_and_observable() -> None:
    email = f"rate-limit-{uuid4()}@example.com"
    with create_client() as client:
        register(client, email)
        successful_attempts = [
            client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": TEST_PASSWORD},
            )
            for _ in range(5)
        ]
        blocked = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": TEST_PASSWORD},
        )

        assert all(response.status_code == 200 for response in successful_attempts)
        assert [response.headers["X-RateLimit-Remaining"] for response in successful_attempts] == [
            "4",
            "3",
            "2",
            "1",
            "0",
        ]
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "rate_limit_exceeded"
        assert int(blocked.headers["Retry-After"]) > 0

        cache_health = client.get("/health/cache")
        assert cache_health.status_code == 200
        assert cache_health.json()["status"] == "ok"
        assert cache_health.json()["rate_limited"] == 1
