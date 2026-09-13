import pytest

from tests.support.api_factory import (
    auth_headers,
    create_client,
    create_tenant,
    with_tenant,
)
from tests.support.api_factory import (
    register_and_login_tokens as register_and_login,
)
from ticketing.cache.backend import (
    CacheMetrics,
    CacheUnavailableError,
    RateLimitDecision,
)

pytestmark = pytest.mark.integration


class UnavailableCache:
    def __init__(self) -> None:
        self.failures = 0
        self.closed = False

    async def get(self, key: str) -> str | None:
        self.failures += 1
        return None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self.failures += 1

    async def delete(self, key: str) -> None:
        self.failures += 1

    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        self.failures += 1
        return RateLimitDecision(
            allowed=True,
            limit=limit,
            remaining=limit,
            retry_after_seconds=0,
            degraded=True,
        )

    async def ping(self) -> None:
        self.failures += 1
        raise CacheUnavailableError("Redis unavailable during test")

    def metrics_snapshot(self) -> CacheMetrics:
        return CacheMetrics(0, 0, 0, 0, self.failures, 0)

    async def close(self) -> None:
        self.closed = True


def test_invitation_context_isolation_leave_and_rejoin() -> None:
    with create_client() as client:
        owner_a = auth_headers(register_and_login(client, "owner-a@example.com"))
        owner_b = auth_headers(register_and_login(client, "owner-b@example.com"))
        member = auth_headers(register_and_login(client, "member@example.com"))

        tenant_a = create_tenant(client, owner_a, name="Alpha Support", slug="alpha-support")
        tenant_b = create_tenant(client, owner_b, name="Beta Support", slug="beta-support")

        duplicate_slug = client.post(
            "/api/v1/tenants",
            headers=owner_a,
            json={"name": "Duplicate", "slug": "alpha-support"},
        )
        assert duplicate_slug.status_code == 409

        invited = client.post(
            f"/api/v1/tenants/{tenant_a}/invitations",
            headers=with_tenant(owner_a, tenant_a),
            json={"email": "member@example.com"},
        )
        assert invited.status_code == 201
        assert invited.json()["status"] == "invited"

        listed_before_acceptance = client.get("/api/v1/tenants", headers=member)
        assert listed_before_acceptance.status_code == 200
        assert listed_before_acceptance.json()[0]["membership_status"] == "invited"

        pending_context = client.get(
            "/api/v1/tenant-context",
            headers=with_tenant(member, tenant_a),
        )
        assert pending_context.status_code == 404

        accepted = client.post(
            f"/api/v1/tenants/{tenant_a}/membership/accept",
            headers=member,
        )
        assert accepted.status_code == 200

        own_context = client.get(
            "/api/v1/tenant-context",
            headers=with_tenant(member, tenant_a),
        )
        assert own_context.status_code == 200

        foreign_context = client.get(
            "/api/v1/tenant-context",
            headers=with_tenant(member, tenant_b),
        )
        assert foreign_context.status_code == 404

        foreign_member_list = client.get(
            f"/api/v1/tenants/{tenant_b}/members",
            headers=with_tenant(owner_a, tenant_a),
        )
        assert foreign_member_list.status_code == 404

        members = client.get(
            f"/api/v1/tenants/{tenant_a}/members",
            headers=with_tenant(owner_a, tenant_a),
        )
        assert members.status_code == 200
        assert {item["email"] for item in members.json()} == {
            "owner-a@example.com",
            "member@example.com",
        }

        owner_leave = client.post(
            f"/api/v1/tenants/{tenant_a}/membership/leave",
            headers=owner_a,
        )
        assert owner_leave.status_code == 409

        member_leave = client.post(
            f"/api/v1/tenants/{tenant_a}/membership/leave",
            headers=member,
        )
        assert member_leave.status_code == 204
        after_leave = client.get(
            "/api/v1/tenant-context",
            headers=with_tenant(member, tenant_a),
        )
        assert after_leave.status_code == 404

        reinvited = client.post(
            f"/api/v1/tenants/{tenant_a}/invitations",
            headers=with_tenant(owner_a, tenant_a),
            json={"email": "member@example.com"},
        )
        assert reinvited.status_code == 201
        assert reinvited.json()["status"] == "invited"
        reaccepted = client.post(
            f"/api/v1/tenants/{tenant_a}/membership/accept",
            headers=member,
        )
        assert reaccepted.status_code == 200


def test_one_user_can_switch_between_multiple_tenants_by_header() -> None:
    with create_client() as client:
        owner = auth_headers(register_and_login(client, "multi-owner@example.com"))
        first = create_tenant(client, owner, name="First Tenant", slug="first-tenant")
        second = create_tenant(client, owner, name="Second Tenant", slug="second-tenant")

        tenants = client.get("/api/v1/tenants", headers=owner)
        assert tenants.status_code == 200
        assert {item["tenant"]["id"] for item in tenants.json()} == {first, second}

        first_context = client.get(
            "/api/v1/tenant-context",
            headers=with_tenant(owner, first),
        )
        second_context = client.get(
            "/api/v1/tenant-context",
            headers=with_tenant(owner, second),
        )
        assert first_context.json()["tenant"]["id"] == first
        assert second_context.json()["tenant"]["id"] == second


def test_rbac_defaults_denials_role_changes_and_tenant_scope() -> None:
    with create_client() as client:
        owner_a = auth_headers(register_and_login(client, "rbac-owner-a@example.com"))
        owner_b = auth_headers(register_and_login(client, "rbac-owner-b@example.com"))
        member = auth_headers(register_and_login(client, "rbac-member@example.com"))
        register_and_login(client, "rbac-candidate@example.com")

        tenant_a = create_tenant(client, owner_a, name="RBAC Alpha", slug="rbac-alpha")
        tenant_b = create_tenant(client, owner_b, name="RBAC Beta", slug="rbac-beta")

        unauthenticated = client.get(
            f"/api/v1/tenants/{tenant_a}/members",
            headers={"X-Tenant-ID": tenant_a},
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["detail"]["code"] == "invalid_token"

        roles_a_response = client.get(
            f"/api/v1/tenants/{tenant_a}/roles",
            headers=with_tenant(owner_a, tenant_a),
        )
        assert roles_a_response.status_code == 200
        roles_a = {item["name"]: item for item in roles_a_response.json()}
        assert set(roles_a) == {"tenant_admin", "agent", "requester"}
        assert set(roles_a["tenant_admin"]["permissions"]) == {
            "customers.read",
            "customers.write",
            "members.read",
            "members.invite",
            "roles.manage",
            "tickets.read.all",
            "tickets.update.all",
            "tickets.manage.workflow",
            "tickets.comment.internal",
        }
        assert set(roles_a["agent"]["permissions"]) == {
            "customers.read",
            "customers.write",
            "members.read",
            "tickets.read.all",
            "tickets.update.all",
            "tickets.manage.workflow",
            "tickets.comment.internal",
        }
        assert roles_a["requester"]["permissions"] == []

        invitation = client.post(
            f"/api/v1/tenants/{tenant_a}/invitations",
            headers=with_tenant(owner_a, tenant_a),
            json={"email": "rbac-member@example.com"},
        )
        assert invitation.status_code == 201
        membership_id = invitation.json()["id"]
        accepted = client.post(
            f"/api/v1/tenants/{tenant_a}/membership/accept",
            headers=member,
        )
        assert accepted.status_code == 200

        requester_cannot_read = client.get(
            f"/api/v1/tenants/{tenant_a}/members",
            headers=with_tenant(member, tenant_a),
        )
        assert requester_cannot_read.status_code == 403
        assert requester_cannot_read.json()["detail"]["code"] == "permission_denied"
        requester_cannot_invite = client.post(
            f"/api/v1/tenants/{tenant_a}/invitations",
            headers=with_tenant(member, tenant_a),
            json={"email": "rbac-candidate@example.com"},
        )
        assert requester_cannot_invite.status_code == 403
        requester_cannot_manage_roles = client.get(
            f"/api/v1/tenants/{tenant_a}/roles",
            headers=with_tenant(member, tenant_a),
        )
        assert requester_cannot_manage_roles.status_code == 403

        owner_can_read = client.get(
            f"/api/v1/tenants/{tenant_a}/members",
            headers=with_tenant(owner_a, tenant_a),
        )
        assert owner_can_read.status_code == 200
        owner_membership_id = next(
            item["membership_id"]
            for item in owner_can_read.json()
            if item["email"] == "rbac-owner-a@example.com"
        )
        owner_cannot_remove_own_admin_role = client.put(
            f"/api/v1/tenants/{tenant_a}/members/{owner_membership_id}/roles",
            headers=with_tenant(owner_a, tenant_a),
            json={"role_ids": [roles_a["requester"]["id"]]},
        )
        assert owner_cannot_remove_own_admin_role.status_code == 409
        assert (
            owner_cannot_remove_own_admin_role.json()["detail"]["code"]
            == "owner_admin_role_required"
        )
        owner_still_manages_roles = client.get(
            f"/api/v1/tenants/{tenant_a}/roles",
            headers=with_tenant(owner_a, tenant_a),
        )
        assert owner_still_manages_roles.status_code == 200

        wrong_path_tenant = client.get(
            f"/api/v1/tenants/{tenant_b}/members",
            headers=with_tenant(owner_a, tenant_a),
        )
        assert wrong_path_tenant.status_code == 404
        assert wrong_path_tenant.json()["detail"]["code"] == "tenant_not_found"

        roles_b_response = client.get(
            f"/api/v1/tenants/{tenant_b}/roles",
            headers=with_tenant(owner_b, tenant_b),
        )
        assert roles_b_response.status_code == 200
        foreign_agent_role_id = next(
            item["id"] for item in roles_b_response.json() if item["name"] == "agent"
        )
        cross_tenant_role = client.put(
            f"/api/v1/tenants/{tenant_a}/members/{membership_id}/roles",
            headers=with_tenant(owner_a, tenant_a),
            json={"role_ids": [foreign_agent_role_id]},
        )
        assert cross_tenant_role.status_code == 404
        assert cross_tenant_role.json()["detail"]["code"] == "tenant_not_found"

        agent_role_id = roles_a["agent"]["id"]
        requester_role_id = roles_a["requester"]["id"]
        promoted = client.put(
            f"/api/v1/tenants/{tenant_a}/members/{membership_id}/roles",
            headers=with_tenant(owner_a, tenant_a),
            json={"role_ids": [agent_role_id]},
        )
        assert promoted.status_code == 204

        agent_can_read = client.get(
            f"/api/v1/tenants/{tenant_a}/members",
            headers=with_tenant(member, tenant_a),
        )
        assert agent_can_read.status_code == 200
        agent_cannot_invite = client.post(
            f"/api/v1/tenants/{tenant_a}/invitations",
            headers=with_tenant(member, tenant_a),
            json={"email": "rbac-candidate@example.com"},
        )
        assert agent_cannot_invite.status_code == 403
        agent_cannot_manage_roles = client.get(
            f"/api/v1/tenants/{tenant_a}/roles",
            headers=with_tenant(member, tenant_a),
        )
        assert agent_cannot_manage_roles.status_code == 403

        demoted = client.put(
            f"/api/v1/tenants/{tenant_a}/members/{membership_id}/roles",
            headers=with_tenant(owner_a, tenant_a),
            json={"role_ids": [requester_role_id]},
        )
        assert demoted.status_code == 204
        permission_removed_immediately = client.get(
            f"/api/v1/tenants/{tenant_a}/members",
            headers=with_tenant(member, tenant_a),
        )
        assert permission_removed_immediately.status_code == 403

        cache_health = client.get("/health/cache")
        assert cache_health.status_code == 200
        metrics = cache_health.json()
        assert metrics["status"] == "ok"
        assert metrics["hits"] >= 2
        assert metrics["misses"] >= 2
        assert metrics["invalidations"] >= 1


def test_redis_failure_keeps_core_permission_checks_available() -> None:
    cache = UnavailableCache()
    with create_client(cache) as client:
        owner = auth_headers(register_and_login(client, "cache-degraded-owner@example.com"))
        tenant = create_tenant(
            client,
            owner,
            name="Cache Degraded Tenant",
            slug="cache-degraded-tenant",
        )

        roles = client.get(
            f"/api/v1/tenants/{tenant}/roles",
            headers=with_tenant(owner, tenant),
        )
        assert roles.status_code == 200
        assert {role["name"] for role in roles.json()} == {
            "tenant_admin",
            "agent",
            "requester",
        }

        cache_health = client.get("/health/cache")
        assert cache_health.status_code == 200
        assert cache_health.json()["status"] == "degraded"
        assert cache_health.json()["failures"] >= 3

    assert cache.closed is True
