"""Add QR traceability tables: batch_events and verification_scans.

Milestone M6 / T-060. The public QR lookup needs an append-only event
timeline per batch and a scan log. The scan log stores SHA-256 hashes of the
scanned token and of the scanner IP address, never the raw values.

Revision ID: 0008_qr_traceability_analytics
Revises: 0007_production_records
Create Date: 2026-10-30 22:15:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_qr_traceability_analytics"
down_revision = "0007_production_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batch_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("production_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("public_text", sa.Text(), nullable=False),
        sa.Column(
            "actor_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'manufactured','quality_checked','released','held','rejected',"
            "'dispatched','qr_issued'"
            ")",
            name="ck_batch_events_event_type",
        ),
    )
    op.create_index("ix_batch_events_batch_id", "batch_events", ["batch_id"])
    op.create_index("ix_batch_events_event_at", "batch_events", ["event_at"])
    op.create_index(
        "ix_batch_events_batch_event_at", "batch_events", ["batch_id", "event_at", "id"]
    )

    op.create_table(
        "verification_scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("production_batches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "result IN ('found','not_found')",
            name="ck_verification_scans_result",
        ),
    )
    op.create_index("ix_verification_scans_batch_id", "verification_scans", ["batch_id"])
    op.create_index("ix_verification_scans_token_hash", "verification_scans", ["token_hash"])


def downgrade() -> None:
    op.drop_index("ix_verification_scans_token_hash", table_name="verification_scans")
    op.drop_index("ix_verification_scans_batch_id", table_name="verification_scans")
    op.drop_table("verification_scans")
    op.drop_index("ix_batch_events_batch_event_at", table_name="batch_events")
    op.drop_index("ix_batch_events_event_at", table_name="batch_events")
    op.drop_index("ix_batch_events_batch_id", table_name="batch_events")
    op.drop_table("batch_events")
