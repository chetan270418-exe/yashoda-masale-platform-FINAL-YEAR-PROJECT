"""Add M3 factory records: procurement, production plans/stages, quality tables, and the
canonical production batch lifecycle.

Changes:
- production_batches: add planned_qty and actual_qty, move the legacy 'hold' (and
  'recalled') rows to 'on_hold', change the server default to 'draft' and replace the
  status CHECK constraint with the canonical batch statuses.
- new tables: suppliers, raw_materials, material_lots, material_receipts,
  material_receipt_lines, production_plans, production_stages, quality_parameters,
  quality_checks, quality_decisions.

Revision ID: 0007_production_records
Revises: 0006_forecast_evaluation_mase
Create Date: 2026-10-30 22:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_production_records"
down_revision = "0006_forecast_evaluation_mase"
branch_labels = None
depends_on = None

# Canonical batch statuses. Keep in sync with app.production.state_machine.BATCH_STATUS_VALUES.
BATCH_STATUS_VALUES = (
    "draft",
    "in_production",
    "quality_pending",
    "released",
    "on_hold",
    "rejected",
    "cancelled",
)

# Legacy statuses of the previous constraint, mapped onto the canonical ones.
LEGACY_STATUS_MAPPING = {
    "hold": "on_hold",
    "recalled": "on_hold",
}


def _batch_status_check_sql(values: tuple[str, ...]) -> str:
    """Render the CHECK expression listing the allowed batch statuses."""
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({rendered})"


def upgrade() -> None:
    # ── 1. production_batches: canonical statuses ──────────────────────────────
    # The old CHECK constraint only knows the legacy statuses, so it is dropped first
    # and the legacy rows are normalised before the new constraint is applied.
    with op.batch_alter_table("production_batches") as batch_op:
        batch_op.drop_constraint("ck_production_batches_status", type_="check")

    for legacy_status, canonical_status in LEGACY_STATUS_MAPPING.items():
        op.execute(
            sa.text(
                f"UPDATE production_batches SET status = '{canonical_status}' "
                f"WHERE status = '{legacy_status}'"
            )
        )

    with op.batch_alter_table("production_batches") as batch_op:
        batch_op.add_column(
            sa.Column("planned_qty", sa.Numeric(14, 3), nullable=True),
        )
        batch_op.add_column(
            sa.Column("actual_qty", sa.Numeric(14, 3), nullable=True),
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.String(20),
            server_default="draft",
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_production_batches_status",
            _batch_status_check_sql(BATCH_STATUS_VALUES),
        )

    # ── 2. Procurement ─────────────────────────────────────────────────────────
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("contact_email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_suppliers_code", "suppliers", ["code"])

    op.create_table(
        "raw_materials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_raw_materials_code", "raw_materials", ["code"])

    op.create_table(
        "material_lots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lot_code", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "supplier_id",
            sa.String(36),
            sa.ForeignKey("suppliers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "raw_material_id",
            sa.String(36),
            sa.ForeignKey("raw_materials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_material_lots_quantity_positive"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_material_lots_unit_cost_nonnegative"),
    )
    op.create_index("ix_material_lots_lot_code", "material_lots", ["lot_code"])
    op.create_index("ix_material_lots_supplier_id", "material_lots", ["supplier_id"])
    op.create_index("ix_material_lots_raw_material_id", "material_lots", ["raw_material_id"])
    op.create_index(
        "ix_material_lots_supplier_material", "material_lots", ["supplier_id", "raw_material_id"]
    )

    op.create_table(
        "material_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("receipt_no", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "supplier_id",
            sa.String(36),
            sa.ForeignKey("suppliers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column(
            "received_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_material_receipts_receipt_no", "material_receipts", ["receipt_no"])
    op.create_index("ix_material_receipts_supplier_id", "material_receipts", ["supplier_id"])
    op.create_index("ix_material_receipts_received_by", "material_receipts", ["received_by"])

    op.create_table(
        "material_receipt_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "receipt_id",
            sa.String(36),
            sa.ForeignKey("material_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "material_lot_id",
            sa.String(36),
            sa.ForeignKey("material_lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_material_receipt_lines_quantity_positive"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_material_receipt_lines_cost_nonnegative"),
    )
    op.create_index(
        "ix_material_receipt_lines_receipt_id", "material_receipt_lines", ["receipt_id"]
    )
    op.create_index(
        "ix_material_receipt_lines_material_lot_id", "material_receipt_lines", ["material_lot_id"]
    )

    # ── 3. Production ──────────────────────────────────────────────────────────
    op.create_table(
        "production_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_no", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "variant_id",
            sa.String(36),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("planned_qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("planned_start", sa.Date(), nullable=False),
        sa.Column("planned_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("planned_qty > 0", name="ck_production_plans_qty_positive"),
        sa.CheckConstraint(
            "planned_end >= planned_start", name="ck_production_plans_dates_ordered"
        ),
    )
    op.create_index("ix_production_plans_plan_no", "production_plans", ["plan_no"])
    op.create_index("ix_production_plans_variant_id", "production_plans", ["variant_id"])
    op.create_index("ix_production_plans_status", "production_plans", ["status"])
    op.create_index("ix_production_plans_created_by", "production_plans", ["created_by"])
    op.create_index(
        "ix_production_plans_variant_status", "production_plans", ["variant_id", "status"]
    )

    op.create_table(
        "production_stages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("production_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_name", sa.String(60), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_production_stages_sequence_nonnegative"),
        sa.UniqueConstraint("batch_id", "sequence", name="uq_production_stages_batch_sequence"),
    )
    op.create_index("ix_production_stages_batch_id", "production_stages", ["batch_id"])
    op.create_index("ix_production_stages_status", "production_stages", ["status"])
    op.create_index(
        "ix_production_stages_batch_sequence", "production_stages", ["batch_id", "sequence"]
    )

    # ── 4. Quality ─────────────────────────────────────────────────────────────
    op.create_table(
        "quality_parameters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        # Limits stay NULL until the sponsor confirms them; nothing is invented here.
        sa.Column("min_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("max_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("is_placeholder", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_quality_parameters_range_ordered",
        ),
    )
    op.create_index("ix_quality_parameters_code", "quality_parameters", ["code"])

    op.create_table(
        "quality_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("production_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parameter_id",
            sa.String(36),
            sa.ForeignKey("quality_parameters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("measured_value", sa.Numeric(12, 4), nullable=False),
        # NULL means "not judged": a placeholder parameter without limits has no verdict.
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column(
            "checked_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_quality_checks_batch_id", "quality_checks", ["batch_id"])
    op.create_index("ix_quality_checks_parameter_id", "quality_checks", ["parameter_id"])
    op.create_index("ix_quality_checks_checked_by", "quality_checks", ["checked_by"])
    op.create_index(
        "ix_quality_checks_batch_parameter", "quality_checks", ["batch_id", "parameter_id"]
    )

    op.create_table(
        "quality_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("production_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column(
            "decided_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "decision IN ('released', 'on_hold', 'rejected')",
            name="ck_quality_decisions_decision",
        ),
    )
    op.create_index("ix_quality_decisions_batch_id", "quality_decisions", ["batch_id"])
    op.create_index("ix_quality_decisions_decision", "quality_decisions", ["decision"])
    op.create_index("ix_quality_decisions_decided_by", "quality_decisions", ["decided_by"])
    op.create_index(
        "ix_quality_decisions_batch_decided", "quality_decisions", ["batch_id", "decided_at"]
    )


def downgrade() -> None:
    # ── 1. Quality ─────────────────────────────────────────────────────────────
    op.drop_index("ix_quality_decisions_batch_decided", table_name="quality_decisions")
    op.drop_index("ix_quality_decisions_decided_by", table_name="quality_decisions")
    op.drop_index("ix_quality_decisions_decision", table_name="quality_decisions")
    op.drop_index("ix_quality_decisions_batch_id", table_name="quality_decisions")
    op.drop_table("quality_decisions")

    op.drop_index("ix_quality_checks_batch_parameter", table_name="quality_checks")
    op.drop_index("ix_quality_checks_checked_by", table_name="quality_checks")
    op.drop_index("ix_quality_checks_parameter_id", table_name="quality_checks")
    op.drop_index("ix_quality_checks_batch_id", table_name="quality_checks")
    op.drop_table("quality_checks")

    op.drop_index("ix_quality_parameters_code", table_name="quality_parameters")
    op.drop_table("quality_parameters")

    # ── 2. Production ──────────────────────────────────────────────────────────
    op.drop_index("ix_production_stages_batch_sequence", table_name="production_stages")
    op.drop_index("ix_production_stages_status", table_name="production_stages")
    op.drop_index("ix_production_stages_batch_id", table_name="production_stages")
    op.drop_table("production_stages")

    op.drop_index("ix_production_plans_variant_status", table_name="production_plans")
    op.drop_index("ix_production_plans_created_by", table_name="production_plans")
    op.drop_index("ix_production_plans_status", table_name="production_plans")
    op.drop_index("ix_production_plans_variant_id", table_name="production_plans")
    op.drop_index("ix_production_plans_plan_no", table_name="production_plans")
    op.drop_table("production_plans")

    # ── 3. Procurement ─────────────────────────────────────────────────────────
    op.drop_index("ix_material_receipt_lines_material_lot_id", table_name="material_receipt_lines")
    op.drop_index("ix_material_receipt_lines_receipt_id", table_name="material_receipt_lines")
    op.drop_table("material_receipt_lines")

    op.drop_index("ix_material_receipts_received_by", table_name="material_receipts")
    op.drop_index("ix_material_receipts_supplier_id", table_name="material_receipts")
    op.drop_index("ix_material_receipts_receipt_no", table_name="material_receipts")
    op.drop_table("material_receipts")

    op.drop_index("ix_material_lots_supplier_material", table_name="material_lots")
    op.drop_index("ix_material_lots_raw_material_id", table_name="material_lots")
    op.drop_index("ix_material_lots_supplier_id", table_name="material_lots")
    op.drop_index("ix_material_lots_lot_code", table_name="material_lots")
    op.drop_table("material_lots")

    op.drop_index("ix_raw_materials_code", table_name="raw_materials")
    op.drop_table("raw_materials")

    op.drop_index("ix_suppliers_code", table_name="suppliers")
    op.drop_table("suppliers")

    # ── 4. production_batches: back to the legacy lifecycle ───────────────────
    with op.batch_alter_table("production_batches") as batch_op:
        batch_op.drop_constraint("ck_production_batches_status", type_="check")

    # The legacy CHECK constraint only knows 'hold', 'released', 'rejected' and
    # 'recalled', so the canonical statuses are normalised before it is restored.
    op.execute(
        sa.text(
            "UPDATE production_batches SET status = 'hold' "
            "WHERE status IN ('draft', 'in_production', 'quality_pending', 'on_hold', 'cancelled')"
        )
    )

    with op.batch_alter_table("production_batches") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(20),
            server_default="hold",
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_production_batches_status",
            "status IN ('hold','released','rejected','recalled')",
        )
        batch_op.drop_column("actual_qty")
        batch_op.drop_column("planned_qty")
