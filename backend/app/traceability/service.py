"""Traceability service layer (M6 / T-060).

Responsibilities:
- issue / rotate the long random public trace token of a batch;
- append publicly readable events to a batch timeline;
- resolve a scanned token to the allow-listed public payload and record the
  scan, whether or not the token resolved.

Security rules honoured here:
- B11: only allow-listed fields ever reach the public payload, and an unknown
  token produces a "No record found" answer that reveals nothing about whether
  the token ever existed.
- rules.md section 4: the raw token and the raw IP address are never stored and
  never written to the audit log - only SHA-256 hashes are kept.
- rules.md section 12: no public text or response field may claim that a pack
  is genuine, authentic or otherwise proven by the QR code.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import log_action
from app.catalog.models import Product, ProductVariant
from app.core.config import get_settings
from app.core.errors import AppError
from app.inventory.models import ProductionBatch
from app.traceability.models import (
    BATCH_EVENT_TYPE_VALUES,
    BatchEvent,
    BatchEventType,
    VerificationScan,
)
from app.traceability.schemas import PublicBatchEvent, PublicTraceabilityResponse

# Length of the random part of a public trace token. 32 bytes of entropy
# (token_urlsafe) gives 256 bits, so tokens cannot be enumerated or guessed.
TOKEN_ENTROPY_BYTES = 32

# Fingerprint length shown in the owner-only scan log. The full hash stays in
# the database; a short prefix is enough to correlate two scans.
FINGERPRINT_LENGTH = 12

# Public-facing wording for the recorded batch status. This is the release
# status we recorded, not an authenticity claim about any pack. Unknown
# statuses fall back to the recorded value so a new lifecycle state never
# breaks the public page.
RELEASE_STATUS_LABELS: dict[str, str] = {
    "released": "released",
    "hold": "on hold",
    "on_hold": "on hold",
    "rejected": "rejected",
    "recalled": "recalled",
    "cancelled": "cancelled",
    "draft": "not released",
    "in_production": "not released",
    "quality_pending": "awaiting quality check",
}

# Phrases that must never appear in public text: they would claim that a pack
# is genuine or certified, which a QR code can never prove (rules.md section 12).
FORBIDDEN_PUBLIC_CLAIMS: tuple[str, ...] = (
    "verified",
    "genuine",
    "authentic",
    "guaranteed",
    "certified",
)

MAX_PUBLIC_TEXT_LENGTH = 500


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a public trace token.

    The token itself is a 256-bit random secret, so a fast unsalted hash is
    the right tool here: the input is already high entropy, there is nothing
    to brute force, and hashing keeps the raw secret out of the database and
    out of every log line.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_ip(ip_address: str | None) -> str:
    """Return a keyed hash of a scanner IP address.

    Same reasoning as :func:`hash_token`: the raw address is personal data
    that must not be stored, and the hash is only used to correlate scans.
    """
    # Keyed (HMAC) hash: a plain SHA-256 of an IPv4 address can be reversed by trying all
    # 4 billion addresses, so the key makes the stored value useless without the secret.
    key = get_settings().jwt_secret.encode("utf-8")
    return hmac.new(key, (ip_address or "unknown").encode("utf-8"), hashlib.sha256).hexdigest()


def fingerprint(value: str) -> str:
    """Return a short, non-reversible prefix of a hash for display."""
    return value[:FINGERPRINT_LENGTH]


def validate_public_text(public_text: str) -> str:
    """Validate and normalise a public event description.

    Raises ``AppError`` when the text is empty, too long, or contains a claim
    that the QR code can prove (authenticity, certification or a guarantee).
    """
    text = (public_text or "").strip()
    if not text:
        raise AppError(
            code="INVALID_PUBLIC_TEXT",
            message="Public text must not be empty.",
            status_code=400,
        )
    if len(text) > MAX_PUBLIC_TEXT_LENGTH:
        raise AppError(
            code="INVALID_PUBLIC_TEXT",
            message=f"Public text must be at most {MAX_PUBLIC_TEXT_LENGTH} characters.",
            status_code=400,
        )
    lowered = text.lower()
    for phrase in FORBIDDEN_PUBLIC_CLAIMS:
        if phrase in lowered:
            raise AppError(
                code="INVALID_PUBLIC_TEXT",
                message=(
                    "Public text must not contain authenticity, certification or "
                    "guarantee claims; a trace link cannot prove them."
                ),
                status_code=400,
            )
    return text


def _get_batch(db: Session, batch_id: uuid.UUID) -> ProductionBatch:
    """Load a production batch or raise a 404 business error."""
    batch = db.get(ProductionBatch, batch_id)
    if batch is None:
        raise AppError(
            code="BATCH_NOT_FOUND",
            message="Production batch not found.",
            status_code=404,
        )
    return batch


def record_batch_event(
    db: Session,
    batch_id: uuid.UUID,
    event_type: BatchEventType | str,
    public_text: str,
    actor_id: uuid.UUID | None,
    event_at: datetime | None = None,
) -> BatchEvent:
    """Append a publicly readable event to a batch timeline.

    The event is written but not committed: the caller owns the transaction so
    that the event and the state change it describes are saved atomically.
    """
    event_value = event_type.value if isinstance(event_type, BatchEventType) else str(event_type)
    if event_value not in BATCH_EVENT_TYPE_VALUES:
        raise AppError(
            code="INVALID_EVENT_TYPE",
            message=f"Event type '{event_value}' is not allowed.",
            status_code=400,
            details={"allowed_event_types": list(BATCH_EVENT_TYPE_VALUES)},
        )

    text = validate_public_text(public_text)
    batch = _get_batch(db, batch_id)

    event = BatchEvent(
        batch_id=batch.id,
        event_type=event_value,
        event_at=event_at or datetime.now(UTC),
        public_text=text,
        actor_id=actor_id,
    )
    db.add(event)
    db.flush()
    return event


def issue_batch_token(
    db: Session,
    batch_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    rotate: bool = False,
) -> dict[str, Any]:
    """Issue (or rotate) the public trace token of a batch.

    The token is a long random string from :func:`secrets.token_urlsafe`, never
    a sequential id, so a scanner cannot walk the id space. Rotating replaces
    the stored token, which immediately stops the previous link from resolving.
    Both the issuing and the rotating action are audit-logged; the raw token
    is never written to the audit log (rules.md section 4).
    """
    batch = _get_batch(db, batch_id)

    previous_token = batch.public_token
    if previous_token and not rotate:
        raise AppError(
            code="TOKEN_ALREADY_ISSUED",
            message=(
                "This batch already has a public trace token. "
                "Rotate it if the QR code must change."
            ),
            status_code=409,
        )

    token = secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)
    batch.public_token = token
    issued_at = datetime.now(UTC)

    public_text = (
        "The public trace link for this batch was re-issued; the previous link no longer resolves."
        if previous_token
        else "A public trace link was issued for this batch."
    )
    record_batch_event(
        db,
        batch_id=batch.id,
        event_type=BatchEventType.QR_ISSUED,
        public_text=public_text,
        actor_id=actor_id,
        event_at=issued_at,
    )

    log_action(
        db,
        actor_id=actor_id,
        action="TRACEABILITY_TOKEN_ROTATED" if previous_token else "TRACEABILITY_TOKEN_ISSUED",
        entity="production_batches",
        entity_id=batch.id,
        before={"has_public_token": bool(previous_token)},
        after={
            "token_fingerprint": fingerprint(hash_token(token)),
            "rotated": bool(previous_token),
        },
    )
    db.commit()

    return {
        "batch_id": batch.id,
        "batch_code": batch.batch_code,
        "public_token": token,
        "issued_at": issued_at,
        "rotated": bool(previous_token),
    }


def _record_scan(
    db: Session,
    *,
    batch_id: uuid.UUID | None,
    token_hash: str,
    ip_hash: str,
    result: str,
) -> VerificationScan:
    """Write one verification scan row and commit it.

    The scan is committed on its own so that it survives the 404 response of
    an unknown token (the request session is discarded when the error is
    raised).
    """
    scan = VerificationScan(
        batch_id=batch_id,
        token_hash=token_hash,
        scanned_at=datetime.now(UTC),
        ip_hash=ip_hash,
        result=result,
    )
    db.add(scan)
    db.commit()
    return scan


def lookup_public_batch(
    db: Session,
    public_token: str,
    ip_address: str | None,
) -> PublicTraceabilityResponse | None:
    """Resolve a scanned token to the allow-listed public payload.

    A scan is always recorded. When the token resolves, the payload contains
    the product name, batch code, manufacture date, expiry date, release
    status and the public event timeline - and nothing else. When it does not
    resolve, ``None`` is returned so the caller can answer "No record found";
    the recorded row then carries ``result='not_found'`` and a null batch id,
    which is indistinguishable from any other failed lookup.
    """
    token = (public_token or "").strip()
    token_hash = hash_token(token)
    ip_hash = hash_ip(ip_address)

    row = db.execute(
        select(ProductionBatch, Product.name)
        .join(ProductVariant, ProductVariant.id == ProductionBatch.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductionBatch.public_token == token)
    ).first()

    if row is None:
        _record_scan(
            db,
            batch_id=None,
            token_hash=token_hash,
            ip_hash=ip_hash,
            result="not_found",
        )
        return None

    batch, product_name = row
    events = db.scalars(
        select(BatchEvent)
        .where(BatchEvent.batch_id == batch.id)
        .order_by(BatchEvent.event_at.asc(), BatchEvent.created_at.asc(), BatchEvent.id.asc())
    ).all()

    payload = PublicTraceabilityResponse(
        product_name=product_name,
        batch_code=batch.batch_code,
        manufactured_on=batch.manufactured_at,
        expires_on=batch.expiry_date,
        release_status=RELEASE_STATUS_LABELS.get(batch.status, batch.status),
        events=[
            PublicBatchEvent(
                event_type=event.event_type,
                event_at=event.event_at,
                public_text=event.public_text,
            )
            for event in events
        ],
    )

    _record_scan(
        db,
        batch_id=batch.id,
        token_hash=token_hash,
        ip_hash=ip_hash,
        result="found",
    )
    return payload


def list_batch_events(
    db: Session,
    batch_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> list[BatchEvent]:
    """Return the full event history of a batch, oldest first (authenticated).

    Authorization is enforced by the router's ``require_roles`` dependency;
    ``actor_id`` is accepted so the call site matches the other service
    functions.
    """
    _get_batch(db, batch_id)
    return list(
        db.scalars(
            select(BatchEvent)
            .where(BatchEvent.batch_id == batch_id)
            .order_by(BatchEvent.event_at.asc(), BatchEvent.created_at.asc(), BatchEvent.id.asc())
        ).all()
    )


def count_verification_scans(db: Session, *, result: str | None = None) -> int:
    """Total number of scans (for pagination), optionally filtered by outcome."""
    query = select(func.count()).select_from(VerificationScan)
    if result:
        query = query.where(VerificationScan.result == result)
    return int(db.scalar(query) or 0)


def list_verification_scans(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    result: str | None = None,
) -> list[VerificationScan]:
    """Return the verification scan log, newest first (owner only)."""
    query = select(VerificationScan)
    if result:
        query = query.where(VerificationScan.result == result)
    query = (
        query.order_by(VerificationScan.scanned_at.desc(), VerificationScan.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(query).all())


__all__ = [
    "FORBIDDEN_PUBLIC_CLAIMS",
    "RELEASE_STATUS_LABELS",
    "TOKEN_ENTROPY_BYTES",
    "count_verification_scans",
    "fingerprint",
    "hash_ip",
    "hash_token",
    "issue_batch_token",
    "list_batch_events",
    "list_verification_scans",
    "lookup_public_batch",
    "record_batch_event",
    "validate_public_text",
]
