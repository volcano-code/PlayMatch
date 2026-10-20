"""Serialized catalog import receipts and immutable provider identities."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("catalog_lock", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("revision", sa.Integer(), nullable=False))
    op.execute("INSERT INTO catalog_lock (id, revision) VALUES (1, 0)")
    op.create_table("catalog_sources",
        sa.Column("provider_id", sa.String(40), primary_key=True),
        sa.Column("game_id", sa.String(80), sa.ForeignKey("games.id"), nullable=False, unique=True),
        sa.Column("observed_at", sa.String(40), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False))
    op.create_table("catalog_imports",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("catalog_imports")
    op.drop_table("catalog_sources")
    op.drop_table("catalog_lock")
