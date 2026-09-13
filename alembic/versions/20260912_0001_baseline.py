"""Establish the migration baseline.

Revision ID: 20260912_0001
Revises: None
Create Date: 2026-09-12
"""

from collections.abc import Sequence

revision: str = "20260912_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mark an empty database as being managed by this project."""


def downgrade() -> None:
    """Return to the state before project-managed schema objects existed."""
