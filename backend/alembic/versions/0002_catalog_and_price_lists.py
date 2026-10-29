"""create catalog categories products variants and price_lists tables

Revision ID: 0002_catalog_and_price_lists
Revises: 0001_initial_auth_and_audit
Create Date: 2026-10-28 14:40:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_catalog_and_price_lists"
down_revision = "0001_initial_auth_and_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_categories_name", "categories", ["name"])

    op.create_table(
        "products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_products_name", "products", ["name"])

    op.create_table(
        "product_variants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sku", sa.String(50), nullable=False, unique=True),
        sa.Column("pack_size_g", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_product_variants_sku", "product_variants", ["sku"])

    op.create_table(
        "price_lists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.String(200), nullable=True),
    )

    op.create_table(
        "price_list_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("price_list_id", sa.String(36), sa.ForeignKey("price_lists.id"), nullable=False),
        sa.Column(
            "variant_id", sa.String(36), sa.ForeignKey("product_variants.id"), nullable=False
        ),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_price_list_items_variant_id", "price_list_items", ["variant_id"])


def downgrade() -> None:
    op.drop_index("ix_price_list_items_variant_id", table_name="price_list_items")
    op.drop_table("price_list_items")
    op.drop_table("price_lists")
    op.drop_index("ix_product_variants_sku", table_name="product_variants")
    op.drop_table("product_variants")
    op.drop_index("ix_products_name", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_categories_name", table_name="categories")
    op.drop_table("categories")
