"""Add composite indexes for stable ticket list queries.

Revision ID: 20260913_0007
Revises: 20260913_0006
Create Date: 2026-09-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260913_0007"
down_revision: str | Sequence[str] | None = "20260913_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_tickets_tenant_created_at", table_name="tickets")
    op.create_index(
        "ix_tickets_tenant_created_id",
        "tickets",
        ["tenant_id", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_tenant_status_created_id",
        "tickets",
        ["tenant_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_tenant_assignee_created_id",
        "tickets",
        ["tenant_id", "assignee_membership_id", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_tenant_priority_created_id",
        "tickets",
        ["tenant_id", "priority", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_tenant_creator_created_id",
        "tickets",
        ["tenant_id", "created_by_membership_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_tenant_creator_created_id", table_name="tickets")
    op.drop_index("ix_tickets_tenant_priority_created_id", table_name="tickets")
    op.drop_index("ix_tickets_tenant_assignee_created_id", table_name="tickets")
    op.drop_index("ix_tickets_tenant_status_created_id", table_name="tickets")
    op.drop_index("ix_tickets_tenant_created_id", table_name="tickets")
    op.create_index(
        "ix_tickets_tenant_created_at",
        "tickets",
        ["tenant_id", "created_at"],
    )
