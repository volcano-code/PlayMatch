"""Explicit query-triggered price consent and transactional in-app outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "watchlist", sa.Column("alert_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "watchlist", sa.Column("alert_state", sa.String(16), nullable=False, server_default="off")
    )
    op.add_column("watchlist", sa.Column("alert_armed_at", sa.Float()))
    op.add_column("watchlist", sa.Column("alert_consent", sa.String(40)))
    op.add_column("watchlist", sa.Column("alert_command", sa.String(64)))
    op.create_table(
        "alert_lock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO alert_lock (id, revision) VALUES (1, 0)")
    op.create_table(
        "price_alert_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("watch_id", sa.String(36), nullable=False),
        sa.Column("alert_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("available_at", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_until", sa.Float()),
        sa.Column("failure_code", sa.String(40)),
        sa.UniqueConstraint("watch_id", "alert_version"),
    )
    for field in ("user_id", "watch_id", "status"):
        op.create_index("ix_price_alert_events_" + field, "price_alert_events", [field])
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("price_alert_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("read_at", sa.String(40)),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])


def downgrade():
    op.drop_table("notifications")
    op.drop_table("price_alert_events")
    op.drop_table("alert_lock")
    for name in (
        "alert_command",
        "alert_consent",
        "alert_armed_at",
        "alert_state",
        "alert_version",
    ):
        op.drop_column("watchlist", name)
