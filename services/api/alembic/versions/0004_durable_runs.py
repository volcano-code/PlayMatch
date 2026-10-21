"""Durable recommendation leases and serialized enqueue admission.

Existing queued/running rows are retained for a new worker to claim. Stop old
pre-v0.4 processes before upgrading; they do not participate in fencing.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run_queue_lock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO run_queue_lock (id, revision) VALUES (1, 0)")
    op.add_column(
        "runs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("runs", sa.Column("lease_token", sa.String(64)))
    op.add_column("runs", sa.Column("lease_expires_at", sa.Float()))
    op.add_column("runs", sa.Column("deadline_at", sa.Float()))
    op.add_column("runs", sa.Column("available_at", sa.Float(), nullable=False, server_default="0"))
    op.create_index("ix_runs_claim", "runs", ["status", "available_at", "lease_expires_at"])


def downgrade():
    op.drop_index("ix_runs_claim", table_name="runs")
    for column in (
        "available_at",
        "deadline_at",
        "lease_expires_at",
        "lease_token",
        "attempt_count",
    ):
        op.drop_column("runs", column)
    op.drop_table("run_queue_lock")
