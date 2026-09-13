"""Add durable background jobs, transactional outbox, and notification delivery guards.

Revision ID: 20260913_0010
Revises: 20260913_0009
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260913_0010"
down_revision: str | Sequence[str] | None = "20260913_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=18), nullable=False),
        sa.Column("status", sa.String(length=9), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "kind IN ('email_notification', 'ticket_export')",
            name=op.f("ck_background_jobs_background_job_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retrying', 'succeeded', 'failed')",
            name=op.f("ck_background_jobs_background_job_status"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_background_jobs_nonnegative_attempt_count")
        ),
        sa.CheckConstraint(
            "max_attempts > 0", name=op.f("ck_background_jobs_positive_max_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_background_jobs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_background_jobs_tenant_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_background_jobs_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "requested_by_membership_id",
            "idempotency_key",
            name="uq_background_jobs_scope_idempotency_key",
        ),
    )
    op.create_index(
        "ix_background_jobs_tenant_created",
        "background_jobs",
        ["tenant_id", "created_at", "id"],
    )
    op.create_index(
        "ix_background_jobs_tenant_status",
        "background_jobs",
        ["tenant_id", "status", "created_at"],
    )

    op.create_table(
        "outbox_messages",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=9), server_default="pending", nullable=False),
        sa.Column("task_name", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("publish_attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            "status IN ('pending', 'published')",
            name=op.f("ck_outbox_messages_outbox_status"),
        ),
        sa.CheckConstraint(
            "publish_attempt_count >= 0",
            name=op.f("ck_outbox_messages_nonnegative_publish_attempt_count"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_outbox_messages_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_outbox_messages_tenant_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
        sa.UniqueConstraint("job_id", name="uq_outbox_messages_job_id"),
    )
    op.create_index(
        "ix_outbox_messages_pending",
        "outbox_messages",
        ["status", "available_at", "created_at"],
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=18), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('sending', 'sent', 'retryable_failure', 'ambiguous_failure')",
            name=op.f("ck_notification_deliveries_notification_delivery_status"),
        ),
        sa.CheckConstraint(
            "attempt_count > 0",
            name=op.f("ck_notification_deliveries_positive_attempt_count"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notification_deliveries_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_notification_deliveries_tenant_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint("job_id", name="uq_notification_deliveries_job_id"),
        sa.UniqueConstraint("message_id", name="uq_notification_deliveries_message_id"),
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("outbox_messages")
    op.drop_table("background_jobs")
