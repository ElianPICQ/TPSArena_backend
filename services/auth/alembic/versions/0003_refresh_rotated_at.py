"""Refresh tokens: rotated_at, so only a rotated token used again counts as stolen

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("rotated_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    # Tokens revoked before this migration have no rotated_at: reusing one is just invalid.


def downgrade() -> None:
    op.drop_column("refresh_tokens", "rotated_at", schema=SCHEMA)
