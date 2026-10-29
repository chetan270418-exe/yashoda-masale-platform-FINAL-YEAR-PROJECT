"""Add M5 ordering service fields to orders and order_items tables.

Revision ID: 0005_m5_ordering_service
Revises: 0004_ai_subsystem
Create Date: 2026-10-29 14:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_m5_ordering_service"
down_revision = "0004_ai_subsystem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Update orders table with new columns and constraints
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column(
                "customer_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_orders_customer_id"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "order_type",
                sa.String(30),
                nullable=False,
                server_default="sales",
            )
        )
        batch_op.add_column(
            sa.Column(
                "idempotency_key",
                sa.String(128),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "total_amount",
                sa.Numeric(12, 2),
                nullable=False,
                server_default="0.00",
            )
        )
        batch_op.add_column(
            sa.Column(
                "currency",
                sa.String(3),
                nullable=False,
                server_default="INR",
            )
        )
        batch_op.create_index("ix_orders_customer_id", ["customer_id"])
        batch_op.create_index("ix_orders_idempotency_key", ["idempotency_key"], unique=True)
        batch_op.drop_constraint("ck_orders_status", type_="check")
        batch_op.create_check_constraint(
            "ck_orders_status",
            "status IN ("
            "'pending', 'reserved', 'packed', 'dispatched', 'delivered', "
            "'cancelled', 'returned', 'open', 'shipped', 'partially_shipped'"
            ")",
        )
        batch_op.create_check_constraint(
            "ck_orders_total_amount_nonnegative",
            "total_amount >= 0",
        )

    # 2. Update order_items table with snapshot columns and constraints
    with op.batch_alter_table("order_items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "unit_price_snapshot",
                sa.Numeric(12, 2),
                nullable=False,
                server_default="0.00",
            )
        )
        batch_op.add_column(
            sa.Column(
                "price_list_code",
                sa.String(30),
                nullable=False,
                server_default="MRP",
            )
        )
        batch_op.add_column(
            sa.Column(
                "line_total",
                sa.Numeric(12, 2),
                nullable=False,
                server_default="0.00",
            )
        )
        batch_op.create_check_constraint(
            "ck_order_items_price_nonnegative",
            "unit_price_snapshot >= 0",
        )
        batch_op.create_check_constraint(
            "ck_order_items_line_total_nonnegative",
            "line_total >= 0",
        )


def downgrade() -> None:
    # 1. Revert order_items modifications
    with op.batch_alter_table("order_items") as batch_op:
        batch_op.drop_constraint("ck_order_items_line_total_nonnegative", type_="check")
        batch_op.drop_constraint("ck_order_items_price_nonnegative", type_="check")
        batch_op.drop_column("line_total")
        batch_op.drop_column("price_list_code")
        batch_op.drop_column("unit_price_snapshot")

    # 2. Normalize orders status before restoring old check constraint
    op.execute(
        sa.text(
            "UPDATE orders SET status = 'open' "
            "WHERE status NOT IN ('open','reserved','partially_shipped','shipped','cancelled')"
        )
    )

    # 3. Revert orders modifications
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint("ck_orders_total_amount_nonnegative", type_="check")
        batch_op.drop_constraint("ck_orders_status", type_="check")
        batch_op.create_check_constraint(
            "ck_orders_status",
            "status IN ('open','reserved','partially_shipped','shipped','cancelled')",
        )
        batch_op.drop_index("ix_orders_idempotency_key")
        batch_op.drop_index("ix_orders_customer_id")
        batch_op.drop_column("currency")
        batch_op.drop_column("total_amount")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("order_type")
        batch_op.drop_column("customer_id")
