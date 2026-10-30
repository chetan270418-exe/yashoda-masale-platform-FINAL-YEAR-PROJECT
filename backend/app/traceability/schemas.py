"""Pydantic schemas for traceability endpoints (M6 / T-060).

The public schemas are an explicit allow-list (B11). They are built field by
field from the service layer - the ORM object is never serialised and filtered
afterwards, so a field that is not on the allow-list cannot reach a scanner.
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.traceability.models import BatchEventType


class PublicBatchEvent(BaseModel):
    """A single public entry of the batch timeline."""

    event_type: str = Field(..., description="Lifecycle event type, e.g. 'manufactured'")
    event_at: datetime = Field(..., description="When the event was recorded")
    public_text: str = Field(
        ..., description="Plain-language, public-safe description of the event"
    )


class PublicTraceabilityResponse(BaseModel):
    """The complete public payload for one batch.

    Allow-list (nothing else may be returned):
    product name, batch code, manufacture date, expiry date, release status
    and the public event timeline.
    """

    product_name: str = Field(..., description="Name of the product this batch was made from")
    batch_code: str = Field(..., description="Public batch code printed on the pack")
    manufactured_on: date | None = Field(
        None, description="Date the batch was manufactured (null when not recorded)"
    )
    expires_on: date = Field(..., description="Date the batch expires")
    release_status: str = Field(
        ..., description="Recorded release status of the batch, e.g. 'released' or 'on hold'"
    )
    events: list[PublicBatchEvent] = Field(
        default_factory=list, description="Public timeline of the batch, oldest first"
    )


class BatchEventCreate(BaseModel):
    """Payload for recording a new batch event."""

    event_type: BatchEventType = Field(..., description="Lifecycle event type")
    public_text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Plain-language, public-safe description. Never include suppliers, "
        "customers, costs or internal identifiers.",
    )


class BatchEventRead(BaseModel):
    """Internal (authenticated) view of a batch event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Event UUID")
    batch_id: uuid.UUID = Field(..., description="Owning production batch UUID")
    event_type: str = Field(..., description="Lifecycle event type")
    event_at: datetime = Field(..., description="When the event happened")
    public_text: str = Field(..., description="Public-facing description of the event")
    actor_id: uuid.UUID | None = Field(None, description="Staff member who recorded the event")
    created_at: datetime = Field(..., description="Row creation timestamp")


class BatchEventListResponse(BaseModel):
    """Envelope for the authenticated batch event history."""

    batch_id: uuid.UUID = Field(..., description="Production batch UUID")
    total: int = Field(..., description="Number of events returned")
    items: list[BatchEventRead] = Field(default_factory=list, description="Events, oldest first")


class BatchTokenResponse(BaseModel):
    """Result of issuing or rotating a batch's public trace token.

    The raw token is returned once, to the staff member who issued it, so it
    can be printed into the QR code. It is never stored in the audit log and
    never echoed by the public endpoint.
    """

    batch_id: uuid.UUID = Field(..., description="Production batch UUID")
    batch_code: str = Field(..., description="Public batch code")
    public_token: str = Field(..., description="The long random public trace token")
    issued_at: datetime = Field(..., description="When the token was issued")
    rotated: bool = Field(..., description="True when an existing token was replaced")


class VerificationScanRead(BaseModel):
    """One row of the owner-only scan log."""

    id: uuid.UUID = Field(..., description="Scan UUID")
    batch_id: uuid.UUID | None = Field(
        None, description="Batch UUID, null when the scanned token did not resolve"
    )
    result: str = Field(..., description="'found' or 'not_found'")
    token_fingerprint: str = Field(
        ..., description="First 12 hex characters of the stored token hash"
    )
    ip_fingerprint: str = Field(..., description="First 12 hex characters of the stored IP hash")
    scanned_at: datetime = Field(..., description="When the scan happened")


class VerificationScanListResponse(BaseModel):
    """Envelope for the owner-only scan log."""

    total: int = Field(..., description="Number of scan rows returned")
    items: list[VerificationScanRead] = Field(default_factory=list, description="Scan rows")


__all__ = [
    "BatchEventCreate",
    "BatchEventListResponse",
    "BatchEventRead",
    "BatchTokenResponse",
    "PublicBatchEvent",
    "PublicTraceabilityResponse",
    "VerificationScanListResponse",
    "VerificationScanRead",
]
