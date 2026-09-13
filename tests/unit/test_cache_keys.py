from uuid import uuid4

import pytest

from ticketing.cache.keys import permission_snapshot_key, rate_limit_key


def test_permission_snapshot_key_is_tenant_and_membership_scoped() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    membership = uuid4()

    key_a = permission_snapshot_key(tenant_a, membership)
    key_b = permission_snapshot_key(tenant_b, membership)

    assert key_a != key_b
    assert str(tenant_a) in key_a
    assert str(membership) in key_a
    assert key_a.startswith("ticketing:v1:tenant:")


def test_rate_limit_key_hashes_sensitive_identity() -> None:
    identity = "127.0.0.1:user@example.com"

    key = rate_limit_key("login", identity)

    assert identity not in key
    assert "user@example.com" not in key
    assert key == rate_limit_key("login", identity)
    assert key != rate_limit_key("login", "127.0.0.1:other@example.com")


def test_rate_limit_key_rejects_unbounded_scope_names() -> None:
    with pytest.raises(ValueError, match="Invalid rate-limit scope"):
        rate_limit_key("../../login", "identity")
