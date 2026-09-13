from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.support.api_factory import (
    TEST_ATTACHMENT_PATH,
    add_member,
    assign_role,
    create_tenant,
    register_and_login,
    role_ids,
    ticket_headers,
    with_tenant,
)
from tests.support.api_factory import (
    create_client as create_test_client,
)

pytestmark = pytest.mark.integration


def create_client() -> TestClient:
    return create_test_client(attachment_max_size_bytes=64)


def test_customer_crud_permissions_conflicts_and_tenant_isolation() -> None:
    with create_client() as client:
        owner_a = register_and_login(client, "customer-owner-a@example.com")
        owner_b = register_and_login(client, "customer-owner-b@example.com")
        agent = register_and_login(client, "customer-agent@example.com")
        requester = register_and_login(client, "customer-requester@example.com")
        tenant_a = create_tenant(client, owner_a, name="Customer Alpha", slug="customer-alpha")
        tenant_b = create_tenant(client, owner_b, name="Customer Beta", slug="customer-beta")

        agent_membership = add_member(
            client,
            owner=owner_a,
            tenant_id=tenant_a,
            member=agent,
            email="customer-agent@example.com",
        )
        add_member(
            client,
            owner=owner_a,
            tenant_id=tenant_a,
            member=requester,
            email="customer-requester@example.com",
        )
        assign_role(
            client,
            owner=owner_a,
            tenant_id=tenant_a,
            membership_id=agent_membership,
            role_id=role_ids(client, owner_a, tenant_a)["agent"],
        )

        created = client.post(
            f"/api/v1/tenants/{tenant_a}/customers",
            headers=with_tenant(owner_a, tenant_a),
            json={"name": "Alice Customer", "email": "Alice@Example.COM", "phone": " 12345 "},
        )
        assert created.status_code == 201
        customer = cast(dict[str, object], created.json())
        customer_id = customer["id"]
        assert isinstance(customer_id, str)
        assert customer["email"] == "alice@example.com"
        assert customer["phone"] == "12345"

        duplicate = client.post(
            f"/api/v1/tenants/{tenant_a}/customers",
            headers=with_tenant(owner_a, tenant_a),
            json={"name": "Duplicate", "email": "alice@example.com"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "customer_email_conflict"

        same_email_other_tenant = client.post(
            f"/api/v1/tenants/{tenant_b}/customers",
            headers=with_tenant(owner_b, tenant_b),
            json={"name": "Other Alice", "email": "alice@example.com"},
        )
        assert same_email_other_tenant.status_code == 201

        requester_denied = client.get(
            f"/api/v1/tenants/{tenant_a}/customers",
            headers=with_tenant(requester, tenant_a),
        )
        assert requester_denied.status_code == 403

        agent_list = client.get(
            f"/api/v1/tenants/{tenant_a}/customers?limit=1&offset=0",
            headers=with_tenant(agent, tenant_a),
        )
        assert agent_list.status_code == 200
        assert [item["id"] for item in agent_list.json()] == [customer_id]

        updated = client.patch(
            f"/api/v1/tenants/{tenant_a}/customers/{customer_id}",
            headers=with_tenant(agent, tenant_a),
            json={"name": "Alice Updated", "phone": None},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Alice Updated"
        assert updated.json()["phone"] is None

        empty_update = client.patch(
            f"/api/v1/tenants/{tenant_a}/customers/{customer_id}",
            headers=with_tenant(agent, tenant_a),
            json={},
        )
        assert empty_update.status_code == 422
        whitespace_name = client.post(
            f"/api/v1/tenants/{tenant_a}/customers",
            headers=with_tenant(agent, tenant_a),
            json={"name": "   ", "email": "whitespace@example.com"},
        )
        assert whitespace_name.status_code == 422

        foreign_customer = client.get(
            f"/api/v1/tenants/{tenant_b}/customers/{customer_id}",
            headers=with_tenant(owner_b, tenant_b),
        )
        assert foreign_customer.status_code == 404
        assert foreign_customer.json()["detail"]["code"] == "customer_not_found"


def test_ticket_crud_limited_updates_and_role_based_data_scope() -> None:
    with create_client() as client:
        owner = register_and_login(client, "ticket-owner@example.com")
        agent = register_and_login(client, "ticket-agent@example.com")
        requester = register_and_login(client, "ticket-requester@example.com")
        other_owner = register_and_login(client, "ticket-other-owner@example.com")
        tenant = create_tenant(client, owner, name="Ticket Alpha", slug="ticket-alpha")
        other_tenant = create_tenant(
            client,
            other_owner,
            name="Ticket Beta",
            slug="ticket-beta",
        )

        agent_membership = add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=agent,
            email="ticket-agent@example.com",
        )
        add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=requester,
            email="ticket-requester@example.com",
        )
        assign_role(
            client,
            owner=owner,
            tenant_id=tenant,
            membership_id=agent_membership,
            role_id=role_ids(client, owner, tenant)["agent"],
        )

        customer_response = client.post(
            f"/api/v1/tenants/{tenant}/customers",
            headers=with_tenant(agent, tenant),
            json={"name": "Ticket Customer", "email": "ticket-customer@example.com"},
        )
        assert customer_response.status_code == 201
        customer_id = customer_response.json()["id"]

        requester_ticket = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(requester, tenant),
            json={"subject": "Cannot sign in", "description": "Login fails"},
        )
        assert requester_ticket.status_code == 201
        requester_ticket_id = requester_ticket.json()["id"]
        assert requester_ticket.json()["status"] == "open"
        assert requester_ticket.json()["priority"] == "normal"

        agent_ticket = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(agent, tenant),
            json={
                "customer_id": customer_id,
                "subject": "Printer offline",
                "description": "Office printer is unavailable",
                "priority": "high",
            },
        )
        assert agent_ticket.status_code == 201
        agent_ticket_id = agent_ticket.json()["id"]

        requester_cannot_link_hidden_customer = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(requester, tenant),
            json={
                "customer_id": customer_id,
                "subject": "Hidden customer",
                "description": "Requester cannot link a customer record",
            },
        )
        assert requester_cannot_link_hidden_customer.status_code == 404

        requester_list = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(requester, tenant),
        )
        assert requester_list.status_code == 200
        assert [item["id"] for item in requester_list.json()["items"]] == [requester_ticket_id]

        hidden_agent_ticket = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{agent_ticket_id}",
            headers=with_tenant(requester, tenant),
        )
        assert hidden_agent_ticket.status_code == 404
        hidden_update = client.patch(
            f"/api/v1/tenants/{tenant}/tickets/{agent_ticket_id}",
            headers=with_tenant(requester, tenant),
            json={"priority": "urgent"},
        )
        assert hidden_update.status_code == 404

        requester_updates_own = client.patch(
            f"/api/v1/tenants/{tenant}/tickets/{requester_ticket_id}",
            headers=with_tenant(requester, tenant),
            json={"priority": "urgent"},
        )
        assert requester_updates_own.status_code == 200
        assert requester_updates_own.json()["priority"] == "urgent"

        agent_list = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(agent, tenant),
        )
        assert agent_list.status_code == 200
        assert {item["id"] for item in agent_list.json()["items"]} == {
            requester_ticket_id,
            agent_ticket_id,
        }
        agent_updates_any = client.patch(
            f"/api/v1/tenants/{tenant}/tickets/{requester_ticket_id}",
            headers=with_tenant(agent, tenant),
            json={"subject": "Cannot sign in after reset"},
        )
        assert agent_updates_any.status_code == 200

        forbidden_workflow_field = client.patch(
            f"/api/v1/tenants/{tenant}/tickets/{requester_ticket_id}",
            headers=with_tenant(agent, tenant),
            json={"status": "closed"},
        )
        assert forbidden_workflow_field.status_code == 422
        whitespace_subject = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(requester, tenant),
            json={"subject": "   ", "description": "Must fail validation"},
        )
        assert whitespace_subject.status_code == 422

        foreign_customer_id = client.post(
            f"/api/v1/tenants/{other_tenant}/customers",
            headers=with_tenant(other_owner, other_tenant),
            json={"name": "Foreign", "email": "foreign@example.com"},
        ).json()["id"]
        cross_tenant_customer = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(agent, tenant),
            json={
                "customer_id": foreign_customer_id,
                "subject": "Invalid customer scope",
                "description": "Must be rejected",
            },
        )
        assert cross_tenant_customer.status_code == 404

        cross_tenant_ticket = client.get(
            f"/api/v1/tenants/{other_tenant}/tickets/{requester_ticket_id}",
            headers=with_tenant(other_owner, other_tenant),
        )
        assert cross_tenant_ticket.status_code == 404

        missing_ticket = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{uuid4()}",
            headers=with_tenant(agent, tenant),
        )
        assert missing_ticket.status_code == 404


def test_ticket_workflow_permissions_transitions_and_atomic_audit_events() -> None:
    with create_client() as client:
        owner = register_and_login(client, "workflow-owner@example.com")
        first_agent = register_and_login(client, "workflow-agent-a@example.com")
        second_agent = register_and_login(client, "workflow-agent-b@example.com")
        requester = register_and_login(client, "workflow-requester@example.com")
        tenant = create_tenant(client, owner, name="Workflow Tenant", slug="workflow-tenant")

        first_agent_membership = add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=first_agent,
            email="workflow-agent-a@example.com",
        )
        second_agent_membership = add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=second_agent,
            email="workflow-agent-b@example.com",
        )
        requester_membership = add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=requester,
            email="workflow-requester@example.com",
        )
        agent_role_id = role_ids(client, owner, tenant)["agent"]
        assign_role(
            client,
            owner=owner,
            tenant_id=tenant,
            membership_id=first_agent_membership,
            role_id=agent_role_id,
        )
        assign_role(
            client,
            owner=owner,
            tenant_id=tenant,
            membership_id=second_agent_membership,
            role_id=agent_role_id,
        )

        created = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(requester, tenant),
            json={
                "subject": "Workflow test ticket",
                "description": "Exercise every ticket action",
            },
        )
        assert created.status_code == 201
        ticket_id = created.json()["id"]
        assert created.json()["assignee_membership_id"] is None

        requester_cannot_claim = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/claim",
            headers=with_tenant(requester, tenant),
            json={},
        )
        assert requester_cannot_claim.status_code == 403

        claimed = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/claim",
            headers=with_tenant(first_agent, tenant),
            json={"note": "Taking ownership"},
        )
        assert claimed.status_code == 200
        assert claimed.json()["status"] == "in_progress"
        assert claimed.json()["assignee_membership_id"] == first_agent_membership

        duplicate_claim = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/claim",
            headers=with_tenant(second_agent, tenant),
            json={},
        )
        assert duplicate_claim.status_code == 409
        assert duplicate_claim.json()["detail"]["code"] == "invalid_ticket_transition"

        unassigned_agent_cannot_resolve = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/resolve",
            headers=with_tenant(second_agent, tenant),
            json={},
        )
        assert unassigned_agent_cannot_resolve.status_code == 403

        requester_cannot_be_assignee = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/transfer",
            headers=with_tenant(first_agent, tenant),
            json={"assignee_membership_id": requester_membership},
        )
        assert requester_cannot_be_assignee.status_code == 404

        transferred = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/transfer",
            headers=with_tenant(first_agent, tenant),
            json={
                "assignee_membership_id": second_agent_membership,
                "note": "Escalating to agent B",
            },
        )
        assert transferred.status_code == 200
        assert transferred.json()["status"] == "in_progress"
        assert transferred.json()["assignee_membership_id"] == second_agent_membership

        resolved = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/resolve",
            headers=with_tenant(second_agent, tenant),
            json={"note": "Issue fixed"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"

        closed = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/close",
            headers=with_tenant(second_agent, tenant),
            json={"note": "Customer confirmed"},
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"

        requester_reopened = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/reopen",
            headers=with_tenant(requester, tenant),
            json={"note": "Problem returned"},
        )
        assert requester_reopened.status_code == 200
        assert requester_reopened.json()["status"] == "open"
        assert requester_reopened.json()["assignee_membership_id"] is None

        invalid_close = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/close",
            headers=with_tenant(owner, tenant),
            json={},
        )
        assert invalid_close.status_code == 409

        reclaimed = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/claim",
            headers=with_tenant(first_agent, tenant),
            json={},
        )
        assert reclaimed.status_code == 200
        owner_resolved = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/resolve",
            headers=with_tenant(owner, tenant),
            json={"note": "Admin override"},
        )
        assert owner_resolved.status_code == 200
        assert owner_resolved.json()["status"] == "resolved"

        events_response = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/events",
            headers=with_tenant(requester, tenant),
        )
        assert events_response.status_code == 200
        events = events_response.json()
        assert [event["event_type"] for event in events] == [
            "created",
            "claimed",
            "transferred",
            "resolved",
            "closed",
            "reopened",
            "claimed",
            "resolved",
        ]
        assert events[1]["from_status"] == "open"
        assert events[1]["to_status"] == "in_progress"
        assert events[2]["from_assignee_membership_id"] == first_agent_membership
        assert events[2]["to_assignee_membership_id"] == second_agent_membership
        assert events[-1]["actor_membership_id"] != first_agent_membership
        assert len(events) == 8


def test_ticket_cursor_pagination_filters_and_stability_during_inserts() -> None:
    with create_client() as client:
        owner = register_and_login(client, "query-owner@example.com")
        tenant = create_tenant(client, owner, name="Query Tenant", slug="query-tenant")
        members = client.get(
            f"/api/v1/tenants/{tenant}/members",
            headers=with_tenant(owner, tenant),
        )
        assert members.status_code == 200
        owner_membership_id = members.json()[0]["membership_id"]

        def create_ticket(subject: str, priority: str) -> dict[str, object]:
            response = client.post(
                f"/api/v1/tenants/{tenant}/tickets",
                headers=ticket_headers(owner, tenant),
                json={
                    "subject": subject,
                    "description": f"Details for {subject}",
                    "priority": priority,
                },
            )
            assert response.status_code == 201
            return cast(dict[str, object], response.json())

        oldest = create_ticket("Network alpha", "normal")
        older = create_ticket("Printer beta", "high")
        newer = create_ticket("Network gamma", "urgent")
        newest = create_ticket("Email alpha", "high")
        older_created_at = older["created_at"]
        newer_created_at = newer["created_at"]
        assert isinstance(older_created_at, str)
        assert isinstance(newer_created_at, str)

        for ticket in (newer, newest):
            claimed = client.post(
                f"/api/v1/tenants/{tenant}/tickets/{ticket['id']}/actions/claim",
                headers=with_tenant(owner, tenant),
                json={},
            )
            assert claimed.status_code == 200
        resolved = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{newest['id']}/actions/resolve",
            headers=with_tenant(owner, tenant),
            json={},
        )
        assert resolved.status_code == 200

        status_filter = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"status": "in_progress"},
        )
        assert status_filter.status_code == 200
        assert [item["id"] for item in status_filter.json()["items"]] == [newer["id"]]

        assignee_filter = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"assignee_membership_id": owner_membership_id},
        )
        assert assignee_filter.status_code == 200
        assert {item["id"] for item in assignee_filter.json()["items"]} == {
            newer["id"],
            newest["id"],
        }

        priority_filter = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"priority": "high"},
        )
        assert priority_filter.status_code == 200
        assert {item["id"] for item in priority_filter.json()["items"]} == {
            older["id"],
            newest["id"],
        }

        keyword_filter = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"q": "alpha"},
        )
        assert keyword_filter.status_code == 200
        assert {item["id"] for item in keyword_filter.json()["items"]} == {
            oldest["id"],
            newest["id"],
        }

        time_filter = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"created_from": older_created_at, "created_to": newer_created_at},
        )
        assert time_filter.status_code == 200
        assert {item["id"] for item in time_filter.json()["items"]} == {
            older["id"],
            newer["id"],
        }

        first_page = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"limit": 2},
        )
        assert first_page.status_code == 200
        first_payload = first_page.json()
        assert len(first_payload["items"]) == 2
        assert isinstance(first_payload["next_cursor"], str)

        inserted_after_first_page = create_ticket("Newest late insert", "low")
        second_page = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"limit": 2, "cursor": first_payload["next_cursor"]},
        )
        assert second_page.status_code == 200
        first_ids = {item["id"] for item in first_payload["items"]}
        second_ids = {item["id"] for item in second_page.json()["items"]}
        assert first_ids.isdisjoint(second_ids)
        assert inserted_after_first_page["id"] not in second_ids
        assert second_page.json()["next_cursor"] is None

        invalid_cursor = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"cursor": "not-a-valid-cursor"},
        )
        assert invalid_cursor.status_code == 422
        assert invalid_cursor.json()["detail"]["code"] == "invalid_cursor"

        invalid_range = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            params={"created_from": newer_created_at, "created_to": older_created_at},
        )
        assert invalid_range.status_code == 422


def test_ticket_creation_idempotency_replay_and_payload_conflict() -> None:
    with create_client() as client:
        owner = register_and_login(client, "idempotency-owner@example.com")
        tenant = create_tenant(
            client,
            owner,
            name="Idempotency Tenant",
            slug="idempotency-tenant",
        )
        headers = ticket_headers(owner, tenant, "ticket-create-key-0001")
        payload = {
            "subject": "Only create this ticket once",
            "description": "Retry-safe request",
            "priority": "high",
        }

        first = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=headers,
            json=payload,
        )
        replay = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=headers,
            json=payload,
        )
        assert first.status_code == replay.status_code == 201
        assert first.json()["id"] == replay.json()["id"]
        assert "Idempotency-Replayed" not in first.headers
        assert replay.headers["Idempotency-Replayed"] == "true"

        conflicting_payload = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=headers,
            json={**payload, "priority": "urgent"},
        )
        assert conflicting_payload.status_code == 409
        assert conflicting_payload.json()["detail"]["code"] == "idempotency_key_conflict"

        tickets = client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
        )
        assert tickets.status_code == 200
        assert [item["id"] for item in tickets.json()["items"]] == [first.json()["id"]]

        events = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{first.json()['id']}/events",
            headers=with_tenant(owner, tenant),
        )
        assert events.status_code == 200
        assert [event["event_type"] for event in events.json()] == ["created"]

        missing_key = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
            json=payload,
        )
        assert missing_key.status_code == 422


def test_concurrent_ticket_creation_with_same_key_has_one_effect() -> None:
    with create_client() as setup_client:
        owner = register_and_login(setup_client, "concurrent-create@example.com")
        tenant = create_tenant(
            setup_client,
            owner,
            name="Concurrent Create",
            slug="concurrent-create",
        )
        headers = ticket_headers(owner, tenant, "concurrent-ticket-key-0001")
        payload = {
            "subject": "Concurrent duplicate request",
            "description": "Both requests must resolve to one ticket",
        }
        barrier = Barrier(2)

        def submit(client: TestClient) -> tuple[int, str, str | None]:
            barrier.wait()
            response = client.post(
                f"/api/v1/tenants/{tenant}/tickets",
                headers=headers,
                json=payload,
            )
            return (
                response.status_code,
                response.json()["id"],
                response.headers.get("Idempotency-Replayed"),
            )

        with (
            create_client() as first_client,
            create_client() as second_client,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [
                executor.submit(submit, first_client),
                executor.submit(submit, second_client),
            ]
            results = [future.result() for future in futures]

        assert [result[0] for result in results] == [201, 201]
        assert len({result[1] for result in results}) == 1
        assert sorted(result[2] for result in results if result[2] is not None) == ["true"]

        tickets = setup_client.get(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=with_tenant(owner, tenant),
        )
        assert tickets.status_code == 200
        assert len(tickets.json()["items"]) == 1
        ticket_id = tickets.json()["items"][0]["id"]
        events = setup_client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/events",
            headers=with_tenant(owner, tenant),
        )
        assert [event["event_type"] for event in events.json()] == ["created"]


def test_concurrent_claim_allows_exactly_one_agent() -> None:
    with create_client() as setup_client:
        owner = register_and_login(setup_client, "claim-owner@example.com")
        first_agent = register_and_login(setup_client, "claim-agent-a@example.com")
        second_agent = register_and_login(setup_client, "claim-agent-b@example.com")
        tenant = create_tenant(
            setup_client,
            owner,
            name="Concurrent Claim",
            slug="concurrent-claim",
        )
        first_membership = add_member(
            setup_client,
            owner=owner,
            tenant_id=tenant,
            member=first_agent,
            email="claim-agent-a@example.com",
        )
        second_membership = add_member(
            setup_client,
            owner=owner,
            tenant_id=tenant,
            member=second_agent,
            email="claim-agent-b@example.com",
        )
        agent_role = role_ids(setup_client, owner, tenant)["agent"]
        for membership_id in (first_membership, second_membership):
            assign_role(
                setup_client,
                owner=owner,
                tenant_id=tenant,
                membership_id=membership_id,
                role_id=agent_role,
            )

        created = setup_client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(owner, tenant, "concurrent-claim-ticket-0001"),
            json={
                "subject": "Claim exactly once",
                "description": "Two agents will race",
            },
        )
        assert created.status_code == 201
        ticket_id = created.json()["id"]
        barrier = Barrier(2)

        def claim(client: TestClient, headers: dict[str, str]) -> tuple[int, object]:
            barrier.wait()
            response = client.post(
                f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/actions/claim",
                headers=with_tenant(headers, tenant),
                json={},
            )
            return response.status_code, response.json()

        with (
            create_client() as first_client,
            create_client() as second_client,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [
                executor.submit(claim, first_client, first_agent),
                executor.submit(claim, second_client, second_agent),
            ]
            results = [future.result() for future in futures]

        assert sorted(result[0] for result in results) == [200, 409]
        successful_payload = next(result[1] for result in results if result[0] == 200)
        assert isinstance(successful_payload, dict)
        assert successful_payload["assignee_membership_id"] in {
            first_membership,
            second_membership,
        }

        events = setup_client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/events",
            headers=with_tenant(owner, tenant),
        )
        assert events.status_code == 200
        assert [event["event_type"] for event in events.json()] == ["created", "claimed"]


def test_ticket_comments_hide_internal_notes_from_requesters() -> None:
    with create_client() as client:
        owner = register_and_login(client, "comment-owner@example.com")
        agent = register_and_login(client, "comment-agent@example.com")
        requester = register_and_login(client, "comment-requester@example.com")
        other_owner = register_and_login(client, "comment-other-owner@example.com")
        tenant = create_tenant(client, owner, name="Comment Tenant", slug="comment-tenant")
        other_tenant = create_tenant(
            client,
            other_owner,
            name="Comment Other Tenant",
            slug="comment-other-tenant",
        )
        agent_membership = add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=agent,
            email="comment-agent@example.com",
        )
        add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=requester,
            email="comment-requester@example.com",
        )
        assign_role(
            client,
            owner=owner,
            tenant_id=tenant,
            membership_id=agent_membership,
            role_id=role_ids(client, owner, tenant)["agent"],
        )
        created_ticket = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(requester, tenant, "comment-ticket-0001"),
            json={"subject": "Comment visibility", "description": "Visibility contract"},
        )
        assert created_ticket.status_code == 201
        ticket_id = created_ticket.json()["id"]

        public_comment = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/comments",
            headers=with_tenant(requester, tenant),
            json={"body": "  Public reply  ", "visibility": "public"},
        )
        assert public_comment.status_code == 201
        assert public_comment.json()["body"] == "Public reply"

        forbidden_internal = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/comments",
            headers=with_tenant(requester, tenant),
            json={"body": "Requester secret", "visibility": "internal"},
        )
        assert forbidden_internal.status_code == 403
        assert forbidden_internal.json()["detail"]["code"] == "permission_denied"

        internal_comment = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/comments",
            headers=with_tenant(agent, tenant),
            json={"body": "Agent-only context", "visibility": "internal"},
        )
        assert internal_comment.status_code == 201

        requester_view = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/comments",
            headers=with_tenant(requester, tenant),
        )
        assert requester_view.status_code == 200
        assert [item["visibility"] for item in requester_view.json()] == ["public"]

        agent_view = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/comments",
            headers=with_tenant(agent, tenant),
        )
        assert agent_view.status_code == 200
        assert [item["visibility"] for item in agent_view.json()] == ["public", "internal"]

        cross_tenant = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/comments",
            headers=with_tenant(other_owner, other_tenant),
        )
        assert cross_tenant.status_code == 404


def test_ticket_attachments_validate_content_and_enforce_download_scope() -> None:
    with create_client() as client:
        owner = register_and_login(client, "attachment-owner@example.com")
        requester = register_and_login(client, "attachment-requester@example.com")
        other_requester = register_and_login(client, "attachment-other-requester@example.com")
        other_owner = register_and_login(client, "attachment-other-owner@example.com")
        tenant = create_tenant(client, owner, name="Attachment Tenant", slug="attachment-tenant")
        other_tenant = create_tenant(
            client,
            other_owner,
            name="Attachment Other Tenant",
            slug="attachment-other-tenant",
        )
        add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=requester,
            email="attachment-requester@example.com",
        )
        add_member(
            client,
            owner=owner,
            tenant_id=tenant,
            member=other_requester,
            email="attachment-other-requester@example.com",
        )
        created_ticket = client.post(
            f"/api/v1/tenants/{tenant}/tickets",
            headers=ticket_headers(requester, tenant, "attachment-ticket-0001"),
            json={"subject": "Attachment security", "description": "Validate uploaded files"},
        )
        assert created_ticket.status_code == 201
        ticket_id = created_ticket.json()["id"]
        png_content = b"\x89PNG\r\n\x1a\nverified-content"

        uploaded = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments",
            headers=with_tenant(requester, tenant),
            files={"file": ("../evidence.png", png_content, "image/png")},
        )
        assert uploaded.status_code == 201
        attachment = uploaded.json()
        assert attachment["original_filename"] == "evidence.png"
        assert attachment["content_type"] == "image/png"
        assert attachment["size_bytes"] == len(png_content)
        assert "object_key" not in attachment

        stored_files = [path for path in TEST_ATTACHMENT_PATH.rglob("*") if path.is_file()]
        assert len(stored_files) == 1
        assert stored_files[0].name != "evidence.png"
        assert stored_files[0].name.isalnum()

        listed = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments",
            headers=with_tenant(owner, tenant),
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [attachment["id"]]

        downloaded = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments/{attachment['id']}",
            headers=with_tenant(requester, tenant),
        )
        assert downloaded.status_code == 200
        assert downloaded.content == png_content
        assert downloaded.headers["content-type"] == "image/png"
        assert "evidence.png" in downloaded.headers["content-disposition"]

        same_tenant_without_ticket_access = client.get(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments/{attachment['id']}",
            headers=with_tenant(other_requester, tenant),
        )
        assert same_tenant_without_ticket_access.status_code == 404

        cross_tenant = client.get(
            f"/api/v1/tenants/{other_tenant}/tickets/{ticket_id}/attachments/{attachment['id']}",
            headers=with_tenant(other_owner, other_tenant),
        )
        assert cross_tenant.status_code == 404

        mismatched_signature = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments",
            headers=with_tenant(requester, tenant),
            files={"file": ("fake.png", b"%PDF-fake", "image/png")},
        )
        assert mismatched_signature.status_code == 415
        assert mismatched_signature.json()["detail"]["code"] == "attachment_content_type_invalid"

        unsupported_extension = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments",
            headers=with_tenant(requester, tenant),
            files={"file": ("payload.exe", b"MZpayload", "application/octet-stream")},
        )
        assert unsupported_extension.status_code == 415

        oversized = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments",
            headers=with_tenant(requester, tenant),
            files={"file": ("large.png", b"\x89PNG\r\n\x1a\n" + b"x" * 65, "image/png")},
        )
        assert oversized.status_code == 413
        assert oversized.json()["detail"]["code"] == "attachment_too_large"

        empty = client.post(
            f"/api/v1/tenants/{tenant}/tickets/{ticket_id}/attachments",
            headers=with_tenant(requester, tenant),
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert empty.status_code == 422
        assert empty.json()["detail"]["code"] == "attachment_empty"
        assert len([path for path in TEST_ATTACHMENT_PATH.rglob("*") if path.is_file()]) == 1
