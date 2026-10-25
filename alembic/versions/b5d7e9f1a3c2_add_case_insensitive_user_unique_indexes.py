"""Add case-insensitive unique indexes on user email and username.

Revision ID: b5d7e9f1a3c2
Revises: a1b2c3d4e5f6
Create Date: 2026-10-25

Registration and login look users up with ``lower(email)`` / ``lower(username)``,
which the case-sensitive unique indexes cannot serve, and two requests that
differ only in case could both be inserted. The new indexes cover every row,
soft-deleted ones included, matching the existing indexes: a deleted account's
email or username is not reusable.

The upgrade fails if existing rows already collide case-insensitively; resolve
those rows first.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5d7e9f1a3c2"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the functional unique indexes."""
    op.create_index(
        "uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True
    )
    op.create_index(
        "uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True
    )


def downgrade() -> None:
    """Drop the functional unique indexes."""
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_index("uq_users_email_lower", table_name="users")
