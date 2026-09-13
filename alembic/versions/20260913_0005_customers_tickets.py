"""Add tenant-scoped customers and tickets.

Revision ID: 20260913_0005
Revises: 20260913_0004
Create Date: 2026-09-13
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260913_0005"
down_revision: str | Sequence[str] | None = "20260913_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_PERMISSIONS = (
    ("10000000-0000-0000-0000-000000000004", "customers.read", "Read tenant customers"),
    (
        "10000000-0000-0000-0000-000000000005",
        "customers.write",
        "Create and update tenant customers",
    ),
    (
        "10000000-0000-0000-0000-000000000006",
        "tickets.read.all",
        "Read every ticket in a tenant",
    ),
    (
        "10000000-0000-0000-0000-000000000007",
        "tickets.update.all",
        "Update every ticket in a tenant",
    ),
)


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
    op.bulk_insert(
        permissions,
        [
            {"id": permission_id, "code": code, "description": description}
            for permission_id, code, description in NEW_PERMISSIONS
        ],
    )

    connection = op.get_bind()
    permission_ids = dict(
        connection.execute(
            sa.select(permissions.c.code, permissions.c.id).where(
                permissions.c.code.in_([item[1] for item in NEW_PERMISSIONS])
            )
        )
        .tuples()
        .all()
    )
    existing_roles = connection.execute(
        sa.select(roles.c.id, roles.c.name).where(roles.c.name.in_(("tenant_admin", "agent")))
    )
    op.bulk_insert(
        role_permissions,
        [
            {
                "id": uuid4(),
                "role_id": role_id,
                "permission_id": permission_ids[code],
            }
            for role_id, _role_name in existing_roles
            for code in permission_ids
        ],
    )

    op.create_unique_constraint(
        "uq_memberships_tenant_id_id",
        "memberships",
        ["tenant_id", "id"],
    )
    op.create_table(
        "customers",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_customers_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint("tenant_id", "email", name="uq_customers_tenant_email"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_customers_tenant_id_id"),
    )
    op.create_index(op.f("ix_customers_tenant_id"), "customers", ["tenant_id"])
    op.create_table(
        "tickets",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=11), nullable=False),
        sa.Column("priority", sa.String(length=6), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name=op.f("ck_tickets_ticket_priority"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'closed')",
            name=op.f("ck_tickets_ticket_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            name="fk_tickets_tenant_customer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_tickets_tenant_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
    )
    op.create_index(op.f("ix_tickets_customer_id"), "tickets", ["customer_id"])
    op.create_index(
        op.f("ix_tickets_created_by_membership_id"),
        "tickets",
        ["created_by_membership_id"],
    )
    op.create_index("ix_tickets_tenant_created_at", "tickets", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("tickets")
    op.drop_table("customers")
    op.drop_constraint("uq_memberships_tenant_id_id", "memberships", type_="unique")
    permission_codes = [item[1] for item in NEW_PERMISSIONS]
    op.execute(
        sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True, value=permission_codes)
        )
    )
