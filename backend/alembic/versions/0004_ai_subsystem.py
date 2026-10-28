"""Add AI subsystem tables: ai_tool_runs, sales_forecasts, forecast_evaluations, order_drafts.

Revision ID: 0004_ai_subsystem
Revises: 0003_fefo_inventory
Create Date: 2026-10-28 21:30:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_ai_subsystem"
down_revision = "0003_fefo_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

    op.create_table(
        "ai_tool_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tool", sa.String(80), nullable=False),
        sa.Column("args", json_type, nullable=False),
        sa.Column("result", json_type, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('success', 'error', 'denied')", name="ck_ai_tool_run_status"
        ),
    )
    op.create_index("ix_ai_tool_runs_user_id", "ai_tool_runs", ["user_id"])
    op.create_index("ix_ai_tool_runs_tool", "ai_tool_runs", ["tool"])
    op.create_index("ix_ai_tool_runs_created_at", "ai_tool_runs", ["created_at"])

    op.create_table(
        "sales_forecasts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "variant_id",
            sa.String(36),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("predicted_quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "predicted_quantity >= 0",
            name="ck_sales_forecast_predicted_quantity_nonneg",
        ),
    )
    op.create_index("ix_sales_forecasts_variant_id", "sales_forecasts", ["variant_id"])
    op.create_index("ix_sales_forecasts_period", "sales_forecasts", ["period"])
    op.create_index("ix_sales_forecasts_model", "sales_forecasts", ["model"])
    op.create_index(
        "ix_sales_forecasts_variant_period_model",
        "sales_forecasts",
        ["variant_id", "period", "model"],
    )

    op.create_table(
        "forecast_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column(
            "variant_id",
            sa.String(36),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("mae", sa.Numeric(12, 4), nullable=False),
        sa.Column("rmse", sa.Numeric(12, 4), nullable=False),
        sa.Column("mape", sa.Numeric(12, 4), nullable=True),
        sa.Column("test_period", sa.String(60), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_forecast_evaluations_model", "forecast_evaluations", ["model"])
    op.create_index("ix_forecast_evaluations_variant_id", "forecast_evaluations", ["variant_id"])

    op.create_table(
        "order_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("items", json_type, nullable=False),
        sa.Column(
            "total_estimated",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0.00",
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("created_by_ai", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_order_drafts_status",
        ),
        sa.CheckConstraint(
            "total_estimated >= 0",
            name="ck_order_drafts_total_estimated_nonnegative",
        ),
    )
    op.create_index("ix_order_drafts_customer_id", "order_drafts", ["customer_id"])
    op.create_index("ix_order_drafts_status", "order_drafts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_order_drafts_status", table_name="order_drafts")
    op.drop_index("ix_order_drafts_customer_id", table_name="order_drafts")
    op.drop_table("order_drafts")
    op.drop_index("ix_forecast_evaluations_variant_id", table_name="forecast_evaluations")
    op.drop_index("ix_forecast_evaluations_model", table_name="forecast_evaluations")
    op.drop_table("forecast_evaluations")
    op.drop_index("ix_sales_forecasts_variant_period_model", table_name="sales_forecasts")
    op.drop_index("ix_sales_forecasts_model", table_name="sales_forecasts")
    op.drop_index("ix_sales_forecasts_period", table_name="sales_forecasts")
    op.drop_index("ix_sales_forecasts_variant_id", table_name="sales_forecasts")
    op.drop_table("sales_forecasts")
    op.drop_index("ix_ai_tool_runs_created_at", table_name="ai_tool_runs")
    op.drop_index("ix_ai_tool_runs_tool", table_name="ai_tool_runs")
    op.drop_index("ix_ai_tool_runs_user_id", table_name="ai_tool_runs")
    op.drop_table("ai_tool_runs")
