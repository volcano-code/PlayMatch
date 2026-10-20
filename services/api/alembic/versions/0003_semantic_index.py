"""Durable immutable vector snapshots and CAS active pointer."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "embedding_indexes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False),
        sa.Column("catalog_hash", sa.String(64), nullable=False),
        sa.Column("text_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("dimensions", sa.Integer()),
        sa.Column("reused_count", sa.Integer(), nullable=False),
        sa.Column("encoded_count", sa.Integer(), nullable=False),
        sa.Column("started_generation", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.String(40)),
        sa.Column("failure_code", sa.String(50)),
        sa.Column("owner", sa.String(36)),
        sa.Column("lease_until", sa.Float(), nullable=False),
    )
    op.create_table(
        "embedding_vectors",
        sa.Column(
            "index_id",
            sa.String(36),
            sa.ForeignKey("embedding_indexes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("game_id", sa.String(80), primary_key=True),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("vector_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "embedding_pointer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("index_id", sa.String(36), sa.ForeignKey("embedding_indexes.id")),
        sa.Column("generation", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO embedding_pointer (id, generation) VALUES (1, 0)")


def downgrade():
    op.drop_table("embedding_pointer")
    op.drop_table("embedding_vectors")
    op.drop_table("embedding_indexes")
