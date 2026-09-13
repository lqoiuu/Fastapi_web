"""Add ticket workflow assignment and immutable audit events.

Revision ID: 20260913_0006
Revises: 20260913_0005
Create Date: 2026-09-13
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260913_0006"
down_revision: str | Sequence[str] | None = "20260913_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKFLOW_PERMISSION = {
    "id": "10000000-0000-0000-0000-000000000008",
    "code": "tickets.manage.workflow",
    "description": "Claim, transfer, resolve, reopen, and close tenant tickets",
}


def upgrade() -> None:
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String(length=80)),
        sa.column("description", sa.String(length=200)),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String(length=50)),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )
    op.bulk_insert(permissions, [WORKFLOW_PERMISSION])
    connection = op.get_bind()
    existing_role_ids = connection.execute(
        sa.select(roles.c.id).where(roles.c.name.in_(("tenant_admin", "agent")))
    ).scalars()
    op.bulk_insert(
        role_permissions,
        [
            {
                "id": uuid4(),
                "role_id": role_id,
                "permission_id": WORKFLOW_PERMISSION["id"],
            }
            for role_id in existing_role_ids
        ],
    )

    op.create_unique_constraint(
        "uq_tickets_tenant_id_id",
        "tickets",
        ["tenant_id", "id"],
    )
    op.add_column("tickets", sa.Column("assignee_membership_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tickets_tenant_assignee_membership",
        "tickets",
        "memberships",
        ["tenant_id", "assignee_membership_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_tickets_assignee_membership_id"),
        "tickets",
        ["assignee_membership_id"],
    )

    ticket_events = op.create_table(
        "ticket_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=11), nullable=False),
        sa.Column("from_status", sa.String(length=11), nullable=True),
        sa.Column("to_status", sa.String(length=11), nullable=False),
        sa.Column("from_assignee_membership_id", sa.Uuid(), nullable=True),
        sa.Column("to_assignee_membership_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'claimed', 'transferred', 'resolved', 'reopened', 'closed')",
            name=op.f("ck_ticket_events_ticket_event_type"),
        ),
        sa.CheckConstraint(
            "from_status IN ('open', 'in_progress', 'resolved', 'closed')",
            name=op.f("ck_ticket_events_ticket_event_from_status"),
        ),
        sa.CheckConstraint(
            "to_status IN ('open', 'in_progress', 'resolved', 'closed')",
            name=op.f("ck_ticket_events_ticket_event_to_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ticket_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.id"],
            name="fk_ticket_events_tenant_ticket",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_events_tenant_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "from_assignee_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_events_tenant_from_assignee_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "to_assignee_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_events_tenant_to_assignee_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_events")),
    )
    op.create_index(
        "ix_ticket_events_tenant_ticket_created",
        "ticket_events",
        ["tenant_id", "ticket_id", "created_at"],
    )

    tickets = sa.table(
        "tickets",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("created_by_membership_id", sa.Uuid()),
        sa.column("status", sa.String(length=11)),
        sa.column("assignee_membership_id", sa.Uuid()),
    )
    existing_tickets = connection.execute(
        sa.select(
            tickets.c.id,
            tickets.c.tenant_id,
            tickets.c.created_by_membership_id,
            tickets.c.status,
            tickets.c.assignee_membership_id,
        )
    )
    op.bulk_insert(
        ticket_events,
        [
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "ticket_id": ticket_id,
                "actor_membership_id": creator_membership_id,
                "event_type": "created",
                "from_status": None,
                "to_status": ticket_status,
                "from_assignee_membership_id": None,
                "to_assignee_membership_id": assignee_membership_id,
                "note": "Backfilled during ticket workflow migration",
            }
            for (
                ticket_id,
                tenant_id,
                creator_membership_id,
                ticket_status,
                assignee_membership_id,
            ) in existing_tickets
        ],
    )


def downgrade() -> None:
    op.drop_table("ticket_events")
    op.drop_index(op.f("ix_tickets_assignee_membership_id"), table_name="tickets")
    op.drop_constraint(
        "fk_tickets_tenant_assignee_membership",
        "tickets",
        type_="foreignkey",
    )
    op.drop_column("tickets", "assignee_membership_id")
    op.drop_constraint("uq_tickets_tenant_id_id", "tickets", type_="unique")
    op.execute(
        sa.text("DELETE FROM permissions WHERE code = :code").bindparams(
            code=WORKFLOW_PERMISSION["code"]
        )
    )
