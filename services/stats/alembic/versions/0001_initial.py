"""Initial stats schema: matches, match players, player stats, achievements

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

SCHEMA = "stats"


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("match_id", sa.Uuid(), primary_key=True),
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("party_id", sa.String(64)),
        sa.Column("map", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "match_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "match_id", sa.Uuid(), sa.ForeignKey("stats.matches.match_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("kills", sa.Integer(), nullable=False),
        sa.Column("deaths", sa.Integer(), nullable=False),
        sa.Column("placement", sa.Integer(), nullable=False),
        sa.Column("xp_gained", sa.Integer(), nullable=False),
        sa.UniqueConstraint("match_id", "user_id", name="uq_match_players_match_user"),
        schema=SCHEMA,
    )
    op.create_index("ix_match_players_user_id", "match_players", ["user_id"], schema=SCHEMA)

    op.create_table(
        "player_stats",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("matches_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deaths", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("xp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "player_achievements",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("stats.player_stats.user_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("achievement_id", sa.String(32), primary_key=True),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("player_achievements", schema=SCHEMA)
    op.drop_table("player_stats", schema=SCHEMA)
    op.drop_table("match_players", schema=SCHEMA)
    op.drop_table("matches", schema=SCHEMA)
