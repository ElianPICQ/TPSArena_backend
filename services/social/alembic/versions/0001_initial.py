"""Initial social schema: players, friend requests, friendships

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "social"


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("bio", sa.String(200), nullable=False, server_default=""),
        sa.Column("avatar", sa.String(32), nullable=False, server_default="default"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "friend_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "from_user_id", sa.Uuid(), sa.ForeignKey("social.players.user_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("to_user_id", sa.Uuid(), sa.ForeignKey("social.players.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("from_user_id", "to_user_id", name="uq_friend_requests_pair"),
        sa.CheckConstraint("from_user_id <> to_user_id", name="ck_friend_requests_not_self"),
        schema=SCHEMA,
    )
    op.create_index("ix_friend_requests_to_user_id", "friend_requests", ["to_user_id"], schema=SCHEMA)

    op.create_table(
        "friendships",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("social.players.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "friend_id", sa.Uuid(), sa.ForeignKey("social.players.user_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("user_id <> friend_id", name="ck_friendships_not_self"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("friendships", schema=SCHEMA)
    op.drop_table("friend_requests", schema=SCHEMA)
    op.drop_table("players", schema=SCHEMA)
