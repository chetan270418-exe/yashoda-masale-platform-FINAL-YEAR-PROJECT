"""Traceability HTTP endpoints (M6 / T-060).

Public:
    GET  /api/v1/traceability/{public_token}  - allow-listed batch lookup (rate limited)

Authenticated:
    POST /api/v1/traceability/batches/{batch_id}/token           - issue a trace token
    POST /api/v1/traceability/batches/{batch_id}/token/rotate    - rotate a trace token
    GET  /api/v1/traceability/batches/{batch_id}/events          - batch event history
    POST /api/v1/traceability/batches/{batch_id}/events          - record a batch event
    GET  /api/v1/traceability/scans                              - owner-only scan log
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.auth.models import User
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.traceability import service
from app.traceability.schemas import (
    BatchEventCreate,
    BatchEventListResponse,
    BatchEventRead,
    BatchTokenResponse,
    PublicTraceabilityResponse,
    VerificationScanListResponse,
    VerificationScanRead,
)

router = APIRouter(prefix="/api/v1/traceability", tags=["traceability"])

# Rate limit for the public lookup page: 30 requests per minute per client IP.
#
# Rationale: the endpoint is unauthenticated and returns only allow-listed
# data, so the limit is not there to hide business information - it bounds
# scanning noise and scripted probing of the token space. 30/minute is
# generous for a person scanning a shelf of packs (a shop's customers share
# one NAT address) while still stopping a naive enumeration run.
traceability_rate_limiter = RateLimiter(max_requests=30, window_seconds=60, name="traceability")

# Reusable role dependencies (singletons avoid the Ruff B008 warning).
require_traceability_staff = require_roles("owner", "factory")
require_traceability_reader = require_roles("owner", "factory", "warehouse")
require_scan_log_reader = require_roles("owner")


@router.get(
    "/scans",
    response_model=VerificationScanListResponse,
    summary="List QR verification scans (owner only)",
)
def list_scans(
    current_user: Annotated[User, Depends(require_scan_log_reader)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(100, ge=1, le=500, description="Maximum number of scan rows to return"),
    offset: int = Query(0, ge=0, description="Number of scan rows to skip"),
    result: str | None = Query(
        None, pattern="^(found|not_found)$", description="Filter by scan outcome"
    ),
) -> VerificationScanListResponse:
    """Owner only: audit log of every QR scan, newest first."""
    scans = service.list_verification_scans(db, limit=limit, offset=offset, result=result)
    return VerificationScanListResponse(
        total=len(scans),
        items=[
            VerificationScanRead(
                id=scan.id,
                batch_id=scan.batch_id,
                result=scan.result,
                token_fingerprint=service.fingerprint(scan.token_hash),
                ip_fingerprint=service.fingerprint(scan.ip_hash),
                scanned_at=scan.scanned_at,
            )
            for scan in scans
        ],
    )


@router.post(
    "/batches/{batch_id}/token",
    response_model=BatchTokenResponse,
    status_code=201,
    summary="Issue the public trace token of a batch",
)
def issue_token(
    batch_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_traceability_staff)],
    db: Annotated[Session, Depends(get_db)],
) -> BatchTokenResponse:
    """Owner/factory: issue the long random trace token of a batch.

    The token is returned once so it can be printed into the QR code. It is
    stored hashed in the scan log only, and never appears in the audit log.
    """
    result = service.issue_batch_token(
        db, batch_id=batch_id, actor_id=current_user.id, rotate=False
    )
    return BatchTokenResponse(
        batch_id=result["batch_id"],
        batch_code=result["batch_code"],
        public_token=result["public_token"],
        issued_at=result["issued_at"],
        rotated=result["rotated"],
    )


@router.post(
    "/batches/{batch_id}/token/rotate",
    response_model=BatchTokenResponse,
    summary="Rotate the public trace token of a batch",
)
def rotate_token(
    batch_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_traceability_staff)],
    db: Annotated[Session, Depends(get_db)],
) -> BatchTokenResponse:
    """Owner/factory: replace the trace token; the previous link stops resolving."""
    result = service.issue_batch_token(db, batch_id=batch_id, actor_id=current_user.id, rotate=True)
    return BatchTokenResponse(
        batch_id=result["batch_id"],
        batch_code=result["batch_code"],
        public_token=result["public_token"],
        issued_at=result["issued_at"],
        rotated=result["rotated"],
    )


@router.get(
    "/batches/{batch_id}/events",
    response_model=BatchEventListResponse,
    summary="Batch event history (owner, factory, warehouse)",
)
def get_batch_events(
    batch_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_traceability_reader)],
    db: Annotated[Session, Depends(get_db)],
) -> BatchEventListResponse:
    """Owner/factory/warehouse: full event timeline of one batch, oldest first."""
    events = service.list_batch_events(db, batch_id=batch_id, actor_id=current_user.id)
    return BatchEventListResponse(
        batch_id=batch_id,
        total=len(events),
        items=[BatchEventRead.model_validate(event) for event in events],
    )


@router.post(
    "/batches/{batch_id}/events",
    response_model=BatchEventRead,
    status_code=201,
    summary="Record a batch event (owner, factory)",
)
def create_batch_event(
    batch_id: uuid.UUID,
    body: BatchEventCreate,
    current_user: Annotated[User, Depends(require_traceability_staff)],
    db: Annotated[Session, Depends(get_db)],
) -> BatchEventRead:
    """Owner/factory: append a publicly readable event to a batch timeline."""
    event = service.record_batch_event(
        db,
        batch_id=batch_id,
        event_type=body.event_type,
        public_text=body.public_text,
        actor_id=current_user.id,
    )
    db.commit()
    return BatchEventRead.model_validate(event)


@router.get(
    "/{public_token}",
    response_model=PublicTraceabilityResponse,
    dependencies=[Depends(traceability_rate_limiter)],
    summary="Public batch lookup by QR trace token",
)
def lookup_public_batch(
    public_token: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> PublicTraceabilityResponse:
    """Public QR lookup.

    Returns the allow-listed payload only: product name, batch code,
    manufacture date, expiry date, release status and the public event
    timeline. An unknown token answers "No record found" and is recorded in
    the scan log with a null batch id, so a failed lookup reveals nothing
    about whether the token ever existed.
    """
    client_ip = request.client.host if request.client else None
    payload = service.lookup_public_batch(db, public_token=public_token, ip_address=client_ip)
    if payload is None:
        raise AppError(
            code="TRACEABILITY_NOT_FOUND",
            message="No record found.",
            status_code=404,
        )
    return payload


__all__ = ["router", "traceability_rate_limiter"]
