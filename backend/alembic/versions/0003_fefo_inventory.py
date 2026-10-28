"""Add FEFO inventory tables: warehouses, production_batches, orders, order_items,
inventory_balances, stock_reservations, and stock_movements.

Revision ID: 0003_fefo_inventory
Revises: 0002_create_catalog_and_price_lists
Create Date: 2026-10-28 19:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_fefo_inventory"
down_revision = "0002_create_catalog_and_price_lists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "warehouses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_warehouses_code", "warehouses", ["code"])

    op.create_table(
        "production_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "variant_id",
            sa.String(36),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("batch_code", sa.String(80), nullable=False, unique=True),
        sa.Column("manufactured_at", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="hold"),
        sa.Column("public_token", sa.String(128), nullable=True, unique=True),
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
            "status IN ('hold','released','rejected','recalled')",
            name="ck_production_batches_status",
        ),
        sa.CheckConstraint(
            "manufactured_at IS NULL OR expiry_date >= manufactured_at",
            name="ck_batch_expiry_after_manufacture",
        ),
    )
    op.create_index(
        "ix_batches_variant_expiry",
        "production_batches",
        ["variant_id", "expiry_date", "id"],
    )
    op.create_index("ix_production_batches_batch_code", "production_batches", ["batch_code"])

    op.create_table(
        "orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_number", sa.String(80), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
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
            "status IN ('open','reserved','partially_shipped','shipped','cancelled')",
            name="ck_orders_status",
        ),
    )
    op.create_index("ix_orders_order_number", "orders", ["order_number"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(36),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            sa.String(36),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
    )
    op.create_index("ix_order_items_order", "order_items", ["order_id"])

    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "warehouse_id",
            sa.String(36),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("production_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("on_hand", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("warehouse_id", "batch_id", name="uq_balance_warehouse_batch"),
        sa.CheckConstraint("on_hand >= 0", name="ck_balance_on_hand_nonnegative"),
        sa.CheckConstraint("reserved >= 0", name="ck_balance_reserved_nonnegative"),
        sa.CheckConstraint("reserved <= on_hand", name="ck_balance_reserved_lte_on_hand"),
    )
    op.create_index(
        "ix_balance_warehouse_batch", "inventory_balances", ["warehouse_id", "batch_id"]
    )

    op.create_table(
        "stock_reservations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "order_item_id",
            sa.String(36),
            sa.ForeignKey("order_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "balance_id",
            sa.String(36),
            sa.ForeignKey("inventory_balances.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
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
        sa.CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
        sa.CheckConstraint(
            "status IN ('active','released','consumed')", name="ck_reservation_status"
        ),
    )
    op.create_index(
        "ix_reservation_order_item_status",
        "stock_reservations",
        ["order_item_id", "status"],
    )
    op.create_index("ix_reservation_balance_status", "stock_reservations", ["balance_id", "status"])

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "warehouse_id",
            sa.String(36),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("production_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "balance_id",
            sa.String(36),
            sa.ForeignKey("inventory_balances.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("movement_type", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("reference_type", sa.String(40), nullable=False),
        sa.Column("reference_id", sa.String(100), nullable=False),
        sa.Column("operation_key", sa.String(160), nullable=False, unique=True),
        sa.Column("reason_category", sa.String(60), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint("quantity > 0", name="ck_movement_quantity_positive"),
        sa.CheckConstraint(
            "movement_type IN ("
            "'receipt', 'reserve', 'release', 'dispatch', 'adjustment_in', 'adjustment_out'"
            ")",
            name="ck_movement_type",
        ),
    )
    op.create_index("ix_movement_batch_created", "stock_movements", ["batch_id", "created_at"])
    op.create_index(
        "ix_movement_warehouse_created", "stock_movements", ["warehouse_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_movement_warehouse_created", table_name="stock_movements")
    op.drop_index("ix_movement_batch_created", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_index("ix_reservation_balance_status", table_name="stock_reservations")
    op.drop_index("ix_reservation_order_item_status", table_name="stock_reservations")
    op.drop_table("stock_reservations")
    op.drop_index("ix_balance_warehouse_batch", table_name="inventory_balances")
    op.drop_table("inventory_balances")
    op.drop_index("ix_order_items_order", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_order_number", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_production_batches_batch_code", table_name="production_batches")
    op.drop_index("ix_batches_variant_expiry", table_name="production_batches")
    op.drop_table("production_batches")
    op.drop_index("ix_warehouses_code", table_name="warehouses")
    op.drop_table("warehouses")
