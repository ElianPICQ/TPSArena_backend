"""Loadouts: the player's saved classes (game equipment)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "social"


def upgrade() -> None:
    op.create_table(
        "loadouts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(24), nullable=False),
        sa.Column("primary_weapon", sa.String(24), nullable=False),
        sa.Column("secondary_weapon", sa.String(24), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("slot >= 0 AND slot < 10", name="ck_loadouts_slot"),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.players.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "slot"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("loadouts", schema=SCHEMA)
