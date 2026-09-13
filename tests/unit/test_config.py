import pytest
from pydantic import SecretStr, ValidationError

from tests.support.api_factory import assert_safe_test_resources, build_test_settings
from ticketing.core.config import Settings


def test_production_rejects_development_auth_secret() -> None:
    with pytest.raises(ValidationError, match="Production requires"):
        Settings(environment="production")


def test_production_accepts_explicit_strong_auth_secret() -> None:
    settings = Settings(
        environment="production",
        auth_secret_key=SecretStr("a-unique-production-secret-with-32-plus-characters"),
    )

    assert settings.environment == "production"


def test_integration_cleanup_rejects_development_database() -> None:
    settings = build_test_settings(
        database_url="postgresql+asyncpg://ticketing:ticketing@127.0.0.1:5432/ticketing"
    )

    with pytest.raises(RuntimeError, match="does not end in '_test'"):
        assert_safe_test_resources(settings)


@pytest.mark.parametrize("redis_database", [0, 1])
def test_integration_cleanup_rejects_application_redis_databases(
    redis_database: int,
) -> None:
    settings = build_test_settings(redis_url=f"redis://127.0.0.1:6379/{redis_database}")

    with pytest.raises(RuntimeError, match="Redis database 0 or 1"):
        assert_safe_test_resources(settings)
