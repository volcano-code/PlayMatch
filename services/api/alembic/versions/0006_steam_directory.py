"""Separate scalable Steam discovery from verified recommendation metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("games", sa.Column("search_text", sa.Text(), nullable=False, server_default=""))
    op.add_column("games", sa.Column("search_themes", sa.Text(), nullable=False, server_default=""))
    # Backfill bounded batches, preserving JSON and all stable game ids.
    games = sa.table(
        "games",
        sa.column("id", sa.String()),
        sa.column("payload", sa.JSON()),
        sa.column("search_text", sa.Text()),
        sa.column("search_themes", sa.Text()),
    )
    connection = op.get_bind()
    last_id = ""
    while True:
        rows = connection.execute(
            sa.select(games.c.id, games.c.payload)
            .where(games.c.id > last_id)
            .order_by(games.c.id)
            .limit(500)
        ).all()
        if not rows:
            break
        for game_id, payload in rows:
            text = " ".join(
                [
                    payload.get("title", ""),
                    payload.get("title_zh", ""),
                    *payload.get("aliases", []),
                    payload.get("description", ""),
                    *payload.get("themes", []),
                    *payload.get("genres", []),
                ]
            ).casefold()
            themes = "\x1f" + "\x1f".join(t.casefold() for t in payload.get("themes", [])) + "\x1f"
            connection.execute(
                games.update()
                .where(games.c.id == game_id)
                .values(search_text=text, search_themes=themes)
            )
        last_id = rows[-1][0]

    op.create_table(
        "steam_directory_apps",
        sa.Column("appid", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(1024), nullable=False),
        sa.Column("name_normalized", sa.String(512), nullable=False),
        sa.Column("source_modified", sa.BigInteger()),
        sa.Column("price_change_number", sa.BigInteger()),
        sa.Column("first_seen", sa.Float(), nullable=False),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.Column("full_generation", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_steam_directory_apps_name_normalized",
        "steam_directory_apps",
        ["name_normalized"],
        postgresql_ops={"name_normalized": "text_pattern_ops"},
    )
    op.create_index(
        "ix_steam_directory_apps_full_generation", "steam_directory_apps", ["full_generation"]
    )
    op.create_table(
        "steam_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        *[
            sa.Column(k, sa.Integer(), nullable=False, server_default="0")
            for k in (
                "generation",
                "completed_generation",
                "failures",
                "pages",
                "rows_seen",
                "requests",
                "request_day",
                "daily_requests",
            )
        ],
        *[
            sa.Column(k, sa.BigInteger(), nullable=False, server_default="0")
            for k in ("cursor", "since", "watermark")
        ],
        *[
            sa.Column(k, sa.Float(), nullable=False, server_default="0")
            for k in ("lease_until", "next_due")
        ],
        *[
            sa.Column(k, sa.Float())
            for k in ("started_at", "last_success", "last_full_success", "heartbeat_at")
        ],
        sa.Column("mode", sa.String(16), nullable=False, server_default="full"),
        sa.Column("cycle_id", sa.String(36)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("last_error", sa.String(40)),
        sa.Column("last_page_hash", sa.String(64)),
    )
    op.execute("INSERT INTO steam_sync_state (id) VALUES (1)")
    op.create_table(
        "steam_sync_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("completed_at", sa.Float(), nullable=False),
        sa.Column("since", sa.BigInteger(), nullable=False),
        sa.Column("watermark", sa.BigInteger(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("rows_seen", sa.Integer(), nullable=False),
        sa.Column("final_cursor", sa.BigInteger(), nullable=False),
        sa.Column("final_page_hash", sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_column("games", "search_themes")
    op.drop_column("games", "search_text")
    op.drop_table("steam_sync_receipts")
    op.drop_table("steam_sync_state")
    op.drop_table("steam_directory_apps")
