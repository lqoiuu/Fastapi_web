"""Add tenant RBAC tables and permission catalog.

Revision ID: 20260913_0004
Revises: 20260913_0003
Create Date: 2026-09-13
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260913_0004"
down_revision: str | Sequence[str] | None = "20260913_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    permissions = op.create_table(
        "permissions",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permissions")),
        sa.UniqueConstraint("code", name=op.f("uq_permissions_code")),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": "10000000-0000-0000-0000-000000000001",
                "code": "members.read",
                "description": "Read tenant memberships",
            },
            {
                "id": "10000000-0000-0000-0000-000000000002",
                "code": "members.invite",
                "description": "Invite or reinvite tenant members",
            },
            {
                "id": "10000000-0000-0000-0000-000000000003",
                "code": "roles.manage",
                "description": "Assign tenant roles",
            },
        ],
    )
    roles = op.create_table(
        "roles",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            name=op.f("fk_roles_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_index(op.f("ix_roles_tenant_id"), "roles", ["tenant_id"])
    role_permissions = op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permissions.id"],
            name=op.f("fk_role_permissions_permission_id_permissions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_role_permissions_role_id_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_permissions")),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )
    op.create_index(
        op.f("ix_role_permissions_permission_id"), "role_permissions", ["permission_id"]
    )
    op.create_index(op.f("ix_role_permissions_role_id"), "role_permissions", ["role_id"])
    membership_roles = op.create_table(
        "membership_roles",
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name=op.f("fk_membership_roles_membership_id_memberships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_membership_roles_role_id_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership_roles")),
        sa.UniqueConstraint("membership_id", "role_id", name="uq_membership_roles_pair"),
    )
    op.create_index(
        op.f("ix_membership_roles_membership_id"), "membership_roles", ["membership_id"]
    )
    op.create_index(op.f("ix_membership_roles_role_id"), "membership_roles", ["role_id"])

    tenants = sa.table(
        "tenants",
        sa.column("id", sa.Uuid()),
        sa.column("owner_user_id", sa.Uuid()),
    )
    memberships = sa.table(
        "memberships",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
    )
    connection = op.get_bind()
    permission_ids = dict(
        connection.execute(sa.select(permissions.c.code, permissions.c.id)).tuples().all()
    )

    for tenant_id, owner_user_id in connection.execute(
        sa.select(tenants.c.id, tenants.c.owner_user_id)
    ):
        role_ids = {
            "tenant_admin": uuid4(),
            "agent": uuid4(),
            "requester": uuid4(),
        }
        op.bulk_insert(
            roles,
            [
                {"id": role_id, "tenant_id": tenant_id, "name": name, "is_system": True}
                for name, role_id in role_ids.items()
            ],
        )
        op.bulk_insert(
            role_permissions,
            [
                {
                    "id": uuid4(),
                    "role_id": role_ids["tenant_admin"],
                    "permission_id": permission_ids[code],
                }
                for code in ("members.read", "members.invite", "roles.manage")
            ]
            + [
                {
                    "id": uuid4(),
                    "role_id": role_ids["agent"],
                    "permission_id": permission_ids["members.read"],
                }
            ],
        )

        existing_memberships = connection.execute(
            sa.select(memberships.c.id, memberships.c.user_id).where(
                memberships.c.tenant_id == tenant_id
            )
        )
        op.bulk_insert(
            membership_roles,
            [
                {
                    "id": uuid4(),
                    "membership_id": membership_id,
                    "role_id": role_ids[
                        "tenant_admin" if user_id == owner_user_id else "requester"
                    ],
                }
                for membership_id, user_id in existing_memberships
            ],
        )


def downgrade() -> None:
    op.drop_table("membership_roles")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("permissions")
