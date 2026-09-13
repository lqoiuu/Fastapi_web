"""Add ticket comments, attachment metadata, and internal-comment permission.

Revision ID: 20260913_0009
Revises: 20260913_0008
Create Date: 2026-09-13
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260913_0009"
down_revision: str | Sequence[str] | None = "20260913_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTERNAL_COMMENT_PERMISSION = {
    "id": "10000000-0000-0000-0000-000000000009",
    "code": "tickets.comment.internal",
    "description": "Create and read internal ticket comments",
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
    op.bulk_insert(permissions, [INTERNAL_COMMENT_PERMISSION])
    connection = op.get_bind()
    permission_id = connection.execute(
        sa.select(permissions.c.id).where(permissions.c.code == INTERNAL_COMMENT_PERMISSION["code"])
    ).scalar_one()
    privileged_roles = connection.execute(
        sa.select(roles.c.id).where(roles.c.name.in_(("tenant_admin", "agent")))
    ).scalars()
    op.bulk_insert(
        role_permissions,
        [
            {"id": uuid4(), "role_id": role_id, "permission_id": permission_id}
            for role_id in privileged_roles
        ],
    )

    op.create_table(
        "ticket_comments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("author_membership_id", sa.Uuid(), nullable=False),
        sa.Column("visibility", sa.String(length=8), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'internal')",
            name=op.f("ck_ticket_comments_comment_visibility"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ticket_comments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "author_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_comments_tenant_author_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.id"],
            name="fk_ticket_comments_tenant_ticket",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_comments")),
    )
    op.create_index(
        "ix_ticket_comments_tenant_ticket_created",
        "ticket_comments",
        ["tenant_id", "ticket_id", "created_at"],
    )
    op.create_table(
        "ticket_attachments",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("uploader_membership_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=200), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name=op.f("ck_ticket_attachments_positive_size"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ticket_attachments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.id"],
            name="fk_ticket_attachments_tenant_ticket",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "uploader_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_ticket_attachments_tenant_uploader_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_attachments")),
        sa.UniqueConstraint(
            "object_key",
            name=op.f("uq_ticket_attachments_object_key"),
        ),
    )
    op.create_index(
        "ix_ticket_attachments_tenant_ticket_created",
        "ticket_attachments",
        ["tenant_id", "ticket_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("ticket_attachments")
    op.drop_table("ticket_comments")
    op.execute(
        sa.delete(sa.table("permissions", sa.column("code", sa.String()))).where(
            sa.column("code") == INTERNAL_COMMENT_PERMISSION["code"]
        )
    )
