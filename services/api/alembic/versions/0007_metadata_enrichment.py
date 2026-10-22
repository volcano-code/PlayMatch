"""Automatic enrichment state, expiring provider metadata and transactional lexical postings."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "games",
        sa.Column("metadata_origin", sa.String(16), nullable=False, server_default="manual"),
    )
    op.add_column("games", sa.Column("metadata_valid_until", sa.Float()))
    op.add_column(
        "games",
        sa.Column(
            "recommendation_blocked", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "search_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute("INSERT INTO search_revision(id) VALUES (1)")
    op.create_table(
        "game_search_terms",
        sa.Column(
            "game_id",
            sa.String(80),
            sa.ForeignKey("games.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("term", sa.String(80), primary_key=True),
        sa.Column("weight", sa.Integer(), nullable=False),
    )
    op.create_index("ix_game_search_terms_term", "game_search_terms", ["term"])
    op.create_table(
        "metadata_jobs",
        sa.Column(
            "appid", sa.BigInteger(), sa.ForeignKey("steam_directory_apps.appid"), primary_key=True
        ),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("available_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("last_error", sa.String(48)),
        sa.Column("last_success", sa.Float()),
        sa.Column("provider_updated", sa.BigInteger()),
        sa.Column("provider_hash", sa.String(64)),
    )
    op.create_index("ix_metadata_jobs_available_at", "metadata_jobs", ["available_at"])
    op.create_index("ix_metadata_jobs_status", "metadata_jobs", ["status"])
    op.create_table(
        "metadata_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_cursor", sa.BigInteger(), nullable=False, server_default="0"),
        *[
            sa.Column(k, sa.Float(), nullable=False, server_default="0")
            for k in ("scan_due", "next_request_at", "lease_until")
        ],
        sa.Column("heartbeat_at", sa.Float()),
        sa.Column("last_success", sa.Float()),
        sa.Column("lease_token", sa.String(64)),
        *[
            sa.Column(k, sa.Integer(), nullable=False, server_default="0")
            for k in ("request_day", "reserved_requests")
        ],
        sa.Column("last_error", sa.String(48)),
    )
    op.execute("INSERT INTO metadata_state(id) VALUES (1)")
    # Bounded backfill; this tokenizer is frozen as postings-v1.
    from playmatch.search_terms import postings

    connection = op.get_bind()
    games = sa.table("games", sa.column("id", sa.String()), sa.column("payload", sa.JSON()))
    table = sa.table(
        "game_search_terms", sa.column("game_id"), sa.column("term"), sa.column("weight")
    )
    cursor = ""
    while True:
        rows = connection.execute(
            sa.select(games).where(games.c.id > cursor).order_by(games.c.id).limit(100)
        ).all()
        if not rows:
            break
        values = [
            {"game_id": row.id, "term": term, "weight": weight}
            for row in rows
            for term, weight in postings(row.payload).items()
        ]
        if values:
            connection.execute(table.insert(), values)
        cursor = rows[-1].id


def downgrade():
    op.drop_table("metadata_state")
    op.drop_table("metadata_jobs")
    op.drop_table("game_search_terms")
    op.drop_table("search_revision")
    op.drop_column("games", "recommendation_blocked")
    op.drop_column("games", "metadata_valid_until")
    op.drop_column("games", "metadata_origin")
