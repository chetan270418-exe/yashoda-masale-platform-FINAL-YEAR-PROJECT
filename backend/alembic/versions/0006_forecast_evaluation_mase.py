"""Add MASE skill score to forecast_evaluations.

MASE is the scale-free measure of whether a forecasting model beat the
seasonal naive baseline:

    MASE = MAE(model) / MAE(seasonal naive, in-sample)

MASE below 1 means the model beat the baseline. Unlike MAE it can be compared
across products of very different sales volumes.

Revision ID: 0006_forecast_evaluation_mase
Revises: 0005_m5_ordering_service
Create Date: 2026-10-30 21:15:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_forecast_evaluation_mase"
down_revision = "0005_m5_ordering_service"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("forecast_evaluations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "mase",
                sa.Numeric(12, 4),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("forecast_evaluations") as batch_op:
        batch_op.drop_column("mase")
