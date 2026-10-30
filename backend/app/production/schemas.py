"""Pydantic schemas for the production module (Milestone M3 / T-031 and T-032).

Adheres to:
- Invariant B12: quantities are Decimal, serialized as strings.
- Rule 49: every state change is requested with an explicit target status.
- Rules.md section 12: quality limits are never invented, so they stay optional.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.production.models import PLACEHOLDER_SOURCE


class ProductionPlanCreate(BaseModel):
    """Payload for creating a production plan."""

    variant_id: uuid.UUID = Field(..., description="Product variant to be produced")
    planned_qty: Decimal = Field(
        ...,
        gt=Decimal("0"),
        decimal_places=3,
        description="Planned quantity, strictly positive (Numeric 14, 3)",
    )
    planned_start: date = Field(..., description="First day of the planned run")
    planned_end: date = Field(..., description="Last day of the planned run")
    notes: str | None = Field(None, max_length=1000, description="Optional plan notes")

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "variant_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "planned_qty": "500.000",
                "planned_start": "2026-11-01",
                "planned_end": "2026-11-05",
            }
        },
    )


class ProductionPlanRead(BaseModel):
    """Read schema for a production plan."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    id: uuid.UUID = Field(..., description="Plan UUID")
    plan_no: str = Field(..., description="Unique plan number")
    variant_id: uuid.UUID = Field(..., description="Product variant UUID")
    planned_qty: Decimal = Field(..., description="Planned quantity (Numeric 14, 3)")
    planned_start: date = Field(..., description="First day of the planned run")
    planned_end: date = Field(..., description="Last day of the planned run")
    status: str = Field(..., description="Plan status: planned, in_progress, completed")
    created_by: uuid.UUID = Field(..., description="User UUID who created the plan")
    notes: str | None = Field(None, description="Optional plan notes")
    created_at: datetime = Field(..., description="UTC creation timestamp")


class BatchCreate(BaseModel):
    """Payload for creating a production batch from a plan."""

    plan_id: uuid.UUID = Field(..., description="Production plan the batch is made from")
    batch_code: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description="Unique batch code printed on the pack",
    )
    manufactured_at: date = Field(..., description="Date the batch was manufactured")
    expiry_date: date = Field(..., description="Date the batch expires")
    actual_qty: Decimal | None = Field(
        None,
        gt=Decimal("0"),
        decimal_places=3,
        description="Produced quantity, when it is already known (Numeric 14, 3)",
    )

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "plan_id": "c1eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "batch_code": "BATCH-2026-11-001",
                "manufactured_at": "2026-11-03",
                "expiry_date": "2027-05-03",
                "actual_qty": "480.000",
            }
        },
    )


class ProductionStageRead(BaseModel):
    """Read schema for one processing step of a batch."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Stage UUID")
    batch_id: uuid.UUID = Field(..., description="Batch UUID")
    stage_name: str = Field(..., description="Name of the processing step")
    sequence: int = Field(..., description="Zero-based position of the step in the batch")
    status: str = Field(..., description="Stage status: pending, in_progress, completed")
    started_at: datetime | None = Field(None, description="When the step started")
    completed_at: datetime | None = Field(None, description="When the step finished")
    notes: str | None = Field(None, description="Optional stage notes")
    created_at: datetime = Field(..., description="UTC creation timestamp")


class BatchRead(BaseModel):
    """Read schema for a production batch."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    id: uuid.UUID = Field(..., description="Batch UUID")
    batch_code: str = Field(..., description="Unique batch code")
    variant_id: uuid.UUID = Field(..., description="Product variant UUID")
    planned_qty: Decimal | None = Field(None, description="Planned quantity (Numeric 14, 3)")
    actual_qty: Decimal | None = Field(None, description="Produced quantity (Numeric 14, 3)")
    manufactured_at: date | None = Field(None, description="Date the batch was manufactured")
    expiry_date: date = Field(..., description="Date the batch expires")
    status: str = Field(
        ...,
        description=(
            "Batch status: draft, in_production, quality_pending, released, on_hold, "
            "rejected, cancelled"
        ),
    )
    created_at: datetime = Field(..., description="UTC creation timestamp")
    stages: list[ProductionStageRead] = Field(
        default_factory=list, description="Processing steps of the batch"
    )


class BatchTransitionRequest(BaseModel):
    """Payload for moving a batch to another lifecycle status."""

    to_status: str = Field(
        ...,
        min_length=1,
        max_length=30,
        description="Target status; must be a legal transition for the current status",
    )
    notes: str | None = Field(None, max_length=1000, description="Optional reason or notes")

    model_config = ConfigDict(json_schema_extra={"example": {"to_status": "in_production"}})


class BatchQuantityRequest(BaseModel):
    """Payload for recording the quantity a batch actually produced."""

    actual_qty: Decimal = Field(
        ...,
        gt=Decimal("0"),
        decimal_places=3,
        description="Produced quantity, strictly positive (Numeric 14, 3)",
    )

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={"example": {"actual_qty": "480.000"}},
    )


class BatchReleaseRequest(BaseModel):
    """Payload for releasing a batch into warehouse stock (T-034)."""

    warehouse_id: uuid.UUID = Field(..., description="Warehouse that receives the batch")

    model_config = ConfigDict(
        json_schema_extra={"example": {"warehouse_id": "d1eebc99-9c0b-4ef8-bb6d-6bb9bd380a33"}}
    )


class BatchReleaseResponse(BaseModel):
    """Read schema of the stock created by releasing a batch (T-034)."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    balance_id: uuid.UUID = Field(..., description="Inventory balance UUID")
    warehouse_id: uuid.UUID = Field(..., description="Warehouse UUID")
    batch_id: uuid.UUID = Field(..., description="Released batch UUID")
    batch_status: str = Field(..., description="Status of the batch after the release")
    on_hand: Decimal = Field(..., description="Stock on hand after the release (Numeric 14, 3)")
    reserved: Decimal = Field(..., description="Reserved quantity (Numeric 14, 3)")
    available: Decimal = Field(
        ..., description="Available stock, on_hand - reserved (Numeric 14, 3)"
    )


class QualityParameterCreate(BaseModel):
    """Payload for registering a quality parameter.

    ``min_value`` and ``max_value`` stay optional on purpose: no limit is invented until the
    sponsor supplies one (rules.md section 12).
    """

    code: str = Field(..., min_length=1, max_length=60, description="Unique parameter code")
    name: str = Field(..., min_length=1, max_length=160, description="Parameter display name")
    unit: str = Field(..., min_length=1, max_length=30, description="Unit of measure")
    min_value: Decimal | None = Field(
        None, decimal_places=4, description="Lower limit, when the sponsor supplied one"
    )
    max_value: Decimal | None = Field(
        None, decimal_places=4, description="Upper limit, when the sponsor supplied one"
    )
    is_placeholder: bool = Field(
        True,
        description=(
            "True while the limits are still 'to be confirmed with sponsor'. "
            f"Placeholder marker: {PLACEHOLDER_SOURCE}"
        ),
    )

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "code": "MOISTURE",
                "name": "Moisture content",
                "unit": "%",
                "is_placeholder": True,
            }
        },
    )


class QualityParameterRead(BaseModel):
    """Read schema for a quality parameter."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    id: uuid.UUID = Field(..., description="Quality parameter UUID")
    code: str = Field(..., description="Unique parameter code")
    name: str = Field(..., description="Parameter display name")
    unit: str = Field(..., description="Unit of measure")
    min_value: Decimal | None = Field(None, description="Lower limit, NULL when unknown")
    max_value: Decimal | None = Field(None, description="Upper limit, NULL when unknown")
    is_placeholder: bool = Field(
        ..., description=f"True while the limits are '{PLACEHOLDER_SOURCE}'"
    )
    is_active: bool = Field(..., description="Inactive parameters are not part of a release")
    created_at: datetime = Field(..., description="UTC creation timestamp")


class QualityCheckCreate(BaseModel):
    """Payload for recording one measured value of a quality parameter."""

    parameter_id: uuid.UUID = Field(..., description="Quality parameter that was measured")
    measured_value: Decimal = Field(..., decimal_places=4, description="Measured value")
    notes: str | None = Field(None, max_length=1000, description="Optional check notes")

    model_config = ConfigDict(
        ser_json_decimal="string",
        json_schema_extra={
            "example": {
                "parameter_id": "e1eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
                "measured_value": "8.5000",
            }
        },
    )


class QualityCheckRead(BaseModel):
    """Read schema for a quality check."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    id: uuid.UUID = Field(..., description="Quality check UUID")
    batch_id: uuid.UUID = Field(..., description="Batch UUID")
    parameter_id: uuid.UUID = Field(..., description="Quality parameter UUID")
    measured_value: Decimal = Field(..., description="Measured value (Numeric 12, 4)")
    passed: bool | None = Field(
        None,
        description=(
            "True/False when the parameter has limits; NULL when the parameter is a "
            "placeholder without limits, so 'no limit' is never read as 'passed'"
        ),
    )
    checked_by: uuid.UUID = Field(..., description="User UUID who recorded the check")
    checked_at: datetime = Field(..., description="UTC timestamp of the check")
    notes: str | None = Field(None, description="Optional check notes")


class QualityDecisionCreate(BaseModel):
    """Payload for recording the release / hold / reject decision on a batch."""

    decision: Literal["released", "on_hold", "rejected"] = Field(
        ..., description="Decision taken on the batch"
    )
    remarks: str | None = Field(None, max_length=1000, description="Optional decision remarks")

    model_config = ConfigDict(
        json_schema_extra={"example": {"decision": "released", "remarks": "All checks passed."}}
    )


class QualityDecisionRead(BaseModel):
    """Read schema for a quality decision."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Quality decision UUID")
    batch_id: uuid.UUID = Field(..., description="Batch UUID")
    decision: str = Field(..., description="Decision: released, on_hold or rejected")
    remarks: str | None = Field(None, description="Optional decision remarks")
    decided_by: uuid.UUID = Field(..., description="User UUID who took the decision")
    decided_at: datetime = Field(..., description="UTC timestamp of the decision")


class QualityParameterStatus(BaseModel):
    """Latest result of one quality parameter on one batch."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    parameter_id: uuid.UUID = Field(..., description="Quality parameter UUID")
    code: str = Field(..., description="Unique parameter code")
    name: str = Field(..., description="Parameter display name")
    unit: str = Field(..., description="Unit of measure")
    is_placeholder: bool = Field(..., description="True while the limits are unconfirmed")
    checked: bool = Field(..., description="Whether the parameter has been checked")
    measured_value: Decimal | None = Field(None, description="Latest measured value")
    passed: bool | None = Field(
        None, description="Latest verdict; NULL when the parameter has no limits"
    )
    checked_at: datetime | None = Field(None, description="When the parameter was last checked")


class BatchQualityStatus(BaseModel):
    """Roll-up of the quality state of a batch."""

    model_config = ConfigDict(from_attributes=True, ser_json_decimal="string")

    batch_id: uuid.UUID = Field(..., description="Batch UUID")
    batch_status: str = Field(..., description="Current batch status")
    released_allowed: bool = Field(
        ...,
        description=(
            "True only when every active parameter has a passing check and no check failed"
        ),
    )
    parameters: list[QualityParameterStatus] = Field(
        default_factory=list, description="Latest result per active parameter"
    )
    outstanding: list[str] = Field(
        default_factory=list, description="Parameter codes that still have to be checked"
    )
    failed: list[str] = Field(
        default_factory=list, description="Parameter codes whose latest check failed"
    )
