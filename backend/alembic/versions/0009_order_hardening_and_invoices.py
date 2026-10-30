"""Order status and idempotency hardening, and invoices & shipments (Milestone M5 / T-052).

Removes legacy order statuses ('open', 'shipped', 'partially_shipped'),
scopes idempotency_key uniqueness per customer (customer_id, idempotency_key),
and adds invoices, shipments, and shipment_events tables.

Revision ID: 0009_order_hardening_and_invoices
Revises: 0008_qr_traceability_analytics
Create Date: 2026-10-30 23:20:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_order_hardening_and_invoices"
down_revision = "0008_qr_traceability_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Normalize legacy statuses before constraint tightening
    op.execute(sa.text("UPDATE orders SET status = 'pending' WHERE status = 'open'"))
    op.execute(
        sa.text(
            "UPDATE orders SET status = 'dispatched' "
            "WHERE status IN ('shipped', 'partially_shipped')"
        )
    )

    # 2. Tighten order status and idempotency constraints on orders table
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("ck_orders_status", type_="check")
        batch_op.create_check_constraint(
            "ck_orders_status",
            "status IN ('pending', 'reserved', 'packed', "
            "'dispatched', 'delivered', 'cancelled', 'returned')",
        )
        batch_op.drop_index("ix_orders_idempotency_key")
        batch_op.create_index("ix_orders_idempotency_key", ["idempotency_key"], unique=False)
        batch_op.create_unique_constraint(
            "uq_order_customer_idempotency", ["customer_id", "idempotency_key"]
        )

    # 3. Create invoices table (T-052)
    op.create_table(
        "invoices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(36),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("invoice_number", sa.String(80), nullable=False, unique=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("payment_status", sa.String(30), nullable=False, server_default="unpaid"),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("payment_reference", sa.String(128), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "payment_status IN ('unpaid', 'paid', 'refunded', 'void')",
            name="ck_invoices_payment_status",
        ),
        sa.CheckConstraint("total_amount >= 0", name="ck_invoices_total_amount_nonnegative"),
    )
    op.create_index("ix_invoices_order_id", "invoices", ["order_id"])
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])
    op.create_index("ix_invoices_payment_status", "invoices", ["payment_status"])

    # 4. Create shipments table (T-052)
    op.create_table(
        "shipments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(36),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("shipment_number", sa.String(80), nullable=False, unique=True),
        sa.Column("carrier", sa.String(100), nullable=False),
        sa.Column("tracking_ref", sa.String(128), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="manifested"),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('manifested', 'in_transit', 'out_for_delivery', "
            "'delivered', 'failed', 'returned')",
            name="ck_shipments_status",
        ),
    )
    op.create_index("ix_shipments_order_id", "shipments", ["order_id"])
    op.create_index("ix_shipments_shipment_number", "shipments", ["shipment_number"])
    op.create_index("ix_shipments_tracking_ref", "shipments", ["tracking_ref"])

    # 5. Create shipment_events table (T-052)
    op.create_table(
        "shipment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "shipment_id",
            sa.String(36),
            sa.ForeignKey("shipments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("location", sa.String(100), nullable=True),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column(
            "event_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "event_type IN ('manifested', 'dispatched', 'in_transit', "
            "'out_for_delivery', 'delivered', 'failed', 'returned')",
            name="ck_shipment_events_event_type",
        ),
    )
    op.create_index("ix_shipment_events_shipment_id", "shipment_events", ["shipment_id"])
    op.create_index("ix_shipment_events_event_at", "shipment_events", ["event_at"])


def downgrade() -> None:
    op.drop_index("ix_shipment_events_event_at", table_name="shipment_events")
    op.drop_index("ix_shipment_events_shipment_id", table_name="shipment_events")
    op.drop_table("shipment_events")

    op.drop_index("ix_shipments_tracking_ref", table_name="shipments")
    op.drop_index("ix_shipments_shipment_number", table_name="shipments")
    op.drop_index("ix_shipments_order_id", table_name="shipments")
    op.drop_table("shipments")

    op.drop_index("ix_invoices_payment_status", table_name="invoices")
    op.drop_index("ix_invoices_invoice_number", table_name="invoices")
    op.drop_index("ix_invoices_customer_id", table_name="invoices")
    op.drop_index("ix_invoices_order_id", table_name="invoices")
    op.drop_table("invoices")

    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("uq_order_customer_idempotency", type_="unique")
        batch_op.drop_index("ix_orders_idempotency_key")
        batch_op.create_index("ix_orders_idempotency_key", ["idempotency_key"], unique=True)
        batch_op.drop_constraint("ck_orders_status", type_="check")
        batch_op.create_check_constraint(
            "ck_orders_status",
            "status IN ('pending', 'reserved', 'packed', 'dispatched', "
            "'delivered', 'cancelled', 'returned', 'open', 'shipped', 'partially_shipped')",
        )
