"""Allow more than one provenance source for a normalized game record."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_sources_new",
        sa.Column("provider_id", sa.String(40), primary_key=True),
        sa.Column("game_id", sa.String(80), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("observed_at", sa.String(40), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.execute(
        "INSERT INTO catalog_sources_new(provider_id, game_id, observed_at, content_hash) "
        "SELECT provider_id, game_id, observed_at, content_hash FROM catalog_sources"
    )
    op.drop_table("catalog_sources")
    op.rename_table("catalog_sources_new", "catalog_sources")


def downgrade():
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT game_id FROM catalog_sources GROUP BY game_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicates:
        raise RuntimeError("Cannot downgrade multi-source provenance with duplicate game_id rows")
    op.create_table(
        "catalog_sources_old",
        sa.Column("provider_id", sa.String(40), primary_key=True),
        sa.Column("game_id", sa.String(80), sa.ForeignKey("games.id"), nullable=False, unique=True),
        sa.Column("observed_at", sa.String(40), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.execute(
        "INSERT INTO catalog_sources_old(provider_id, game_id, observed_at, content_hash) "
        "SELECT provider_id, game_id, observed_at, content_hash FROM catalog_sources"
    )
    op.drop_table("catalog_sources")
    op.rename_table("catalog_sources_old", "catalog_sources")
