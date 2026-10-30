"""Tests for the QR traceability module (Milestone M6 / T-060).

Covers the acceptance condition of spec-pack/task.md:60 - "scanning a QR
shows the correct recorded events" - and the privacy rules of B11: the public
endpoint returns an allow-listed payload only, an unknown token answers "No
record found", and neither the raw token nor the raw IP address is ever
stored.
"""

import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.core.db import get_db
from app.core.errors import AppError, register_error_handlers
from app.inventory.models import (
    InventoryBalance,
    Order,
    OrderItem,
    ProductionBatch,
    StockMovement,
    Warehouse,
)
from app.traceability import service
from app.traceability.models import VerificationScan
from app.traceability.router import router as traceability_router
from app.traceability.router import traceability_rate_limiter
from tests.conftest import _make_user, _override_get_db

# Sentinel values that must never reach a public response body.
SUPPLIER_NAME = "SUPPLIER_SECRET_MASALA_TRADERS"
WAREHOUSE_NAME = "WAREHOUSE_SECRET_BACK_ROOM"
CUSTOMER_EMAIL = "customer-secret@example.com"
SECRET_QUANTITY = Decimal("424242")
SECRET_UNIT_PRICE = Decimal("13.37")
SECRET_LINE_TOTAL = Decimal("5672115.54")

ALLOWED_PUBLIC_FIELDS = {
    "product_name",
    "batch_code",
    "manufactured_on",
    "expires_on",
    "release_status",
    "events",
}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The rate limiter keeps module level state, so it is reset around each test."""
    traceability_rate_limiter.reset()
    yield
    traceability_rate_limiter.reset()


@pytest.fixture()
def client():
    """A test client with the traceability router mounted."""
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(traceability_router)
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers(db: Session):
    """Build a bearer token for a freshly created user of the given role."""

    def _headers(role_name: str) -> dict[str, str]:
        email = f"user-{role_name}-{uuid.uuid4().hex[:6]}@example.com"
        user = _make_user(db, email=email, role_name=role_name, password="TestPassword123!")
        token = create_access_token(
            {"sub": str(user.id), "role": user.role.name if user.role else ""}
        )
        return {"Authorization": f"Bearer {token}"}

    return _headers


def _seed_batch(
    db: Session,
    *,
    status: str = "released",
    expiry_in_days: int = 180,
    product_name: str = "Garam Masala",
) -> tuple[ProductionBatch, ProductVariant, Product]:
    """Create a category, product, variant and one production batch."""
    category = Category(name=f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(category)
    db.flush()
    product = Product(name=product_name, category_id=category.id)
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"SKU-{uuid.uuid4().hex[:6]}",
        pack_size_g=100,
    )
    db.add(variant)
    db.flush()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:8]}",
        manufactured_at=date.today() - timedelta(days=10),
        expiry_date=date.today() + timedelta(days=expiry_in_days),
        status=status,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch, variant, product


def _attach_private_data(
    db: Session,
    batch: ProductionBatch,
    variant: ProductVariant,
    customer: User,
) -> None:
    """Attach the private data that must never leak into the public payload.

    A stock movement note carries the supplier name, an inventory balance
    points at the warehouse, and a priced order belongs to a customer.
    """
    warehouse = Warehouse(code=f"WH-{uuid.uuid4().hex[:6]}", name=WAREHOUSE_NAME)
    db.add(warehouse)
    db.flush()

    balance = InventoryBalance(
        warehouse_id=warehouse.id,
        batch_id=batch.id,
        on_hand=Decimal("100"),
        reserved=Decimal("0"),
    )
    db.add(balance)
    db.flush()

    movement = StockMovement(
        warehouse_id=warehouse.id,
        batch_id=batch.id,
        balance_id=balance.id,
        movement_type="receipt",
        quantity=Decimal("100"),
        reference_type="batch",
        reference_id=batch.batch_code,
        operation_key=f"op-{uuid.uuid4().hex[:10]}",
        notes=f"Received from supplier {SUPPLIER_NAME}",
    )
    db.add(movement)

    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:8]}",
        customer_id=customer.id,
        order_type="sales",
        status="delivered",
        total_amount=SECRET_LINE_TOTAL,
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            variant_id=variant.id,
            quantity=SECRET_QUANTITY,
            unit_price_snapshot=SECRET_UNIT_PRICE,
            price_list_code="MRP",
            line_total=SECRET_LINE_TOTAL,
        )
    )
    db.commit()


def _issue_token(db: Session, batch: ProductionBatch) -> str:
    """Give a batch a public token directly (the API path is tested separately)."""
    token = secrets.token_urlsafe(32)
    batch.public_token = token
    db.commit()
    return token


def test_unknown_token_returns_no_record_found(client: TestClient, db: Session):
    """An unknown token answers 404 with the standard error envelope."""
    resp = client.get("/api/v1/traceability/no-such-token")

    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "details"}
    assert body["code"] == "TRACEABILITY_NOT_FOUND"
    assert "no record found" in body["message"].lower()
    # The answer must not echo the token or hint at the token space.
    assert "no-such-token" not in resp.text


def test_unknown_token_is_logged_as_not_found(client: TestClient, db: Session):
    """A failed lookup still writes a scan row with a null batch id."""
    client.get("/api/v1/traceability/no-such-token")

    db.expire_all()
    scans = db.scalars(select(VerificationScan)).all()
    assert len(scans) == 1
    assert scans[0].result == "not_found"
    assert scans[0].batch_id is None


def test_known_token_returns_only_allow_listed_fields(client: TestClient, db: Session):
    """A known token returns exactly the public allow-list."""
    batch, _variant, product = _seed_batch(db)
    token = _issue_token(db, batch)

    resp = client.get(f"/api/v1/traceability/{token}")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == ALLOWED_PUBLIC_FIELDS
    assert body["product_name"] == product.name
    assert body["batch_code"] == batch.batch_code
    assert body["manufactured_on"] == batch.manufactured_at.isoformat()
    assert body["expires_on"] == batch.expiry_date.isoformat()
    assert body["release_status"] == "released"
    assert body["events"] == []


def test_public_response_never_leaks_private_fields(client: TestClient, db: Session):
    """The public payload must not contain supplier, customer, cost or quantity data."""
    batch, variant, _product = _seed_batch(db)
    customer = _make_user(db, email=CUSTOMER_EMAIL, role_name="dealer")
    _attach_private_data(db, batch, variant, customer)
    token = _issue_token(db, batch)

    resp = client.get(f"/api/v1/traceability/{token}")

    assert resp.status_code == 200
    text = resp.text

    # Nothing private may appear anywhere in the serialised body.
    assert SUPPLIER_NAME not in text
    assert WAREHOUSE_NAME not in text
    assert CUSTOMER_EMAIL not in text
    assert str(customer.id) not in text
    assert str(batch.id) not in text
    assert str(variant.id) not in text
    assert str(SECRET_QUANTITY) not in text
    assert str(SECRET_UNIT_PRICE) not in text
    assert str(SECRET_LINE_TOTAL) not in text

    # The structural guarantee: only allow-listed keys are present.
    body = resp.json()
    assert set(body.keys()) == ALLOWED_PUBLIC_FIELDS
    for event in body["events"]:
        assert set(event.keys()) == {"event_type", "event_at", "public_text"}


def test_raw_token_is_never_stored(client: TestClient, db: Session):
    """Only a hash of the token reaches the database."""
    batch, _variant, _product = _seed_batch(db)
    token = _issue_token(db, batch)

    resp = client.get(f"/api/v1/traceability/{token}")
    assert resp.status_code == 200

    db.expire_all()
    scans = db.scalars(select(VerificationScan)).all()
    assert len(scans) == 1
    scan = scans[0]
    assert scan.result == "found"
    assert scan.batch_id == batch.id
    assert scan.token_hash == service.hash_token(token)
    assert len(scan.token_hash) == 64

    for column in scan.__table__.columns:
        value = getattr(scan, column.name)
        if isinstance(value, str):
            assert token not in value


def test_raw_ip_address_is_never_stored(client: TestClient, db: Session):
    """Only a hash of the scanner IP address reaches the database."""
    batch, _variant, _product = _seed_batch(db)
    token = _issue_token(db, batch)

    resp = client.get(f"/api/v1/traceability/{token}")
    assert resp.status_code == 200

    db.expire_all()
    scans = db.scalars(select(VerificationScan)).all()
    assert len(scans) == 1
    scan = scans[0]
    assert len(scan.ip_hash) == 64
    assert scan.ip_hash == service.hash_ip("testclient")

    for column in scan.__table__.columns:
        value = getattr(scan, column.name)
        if isinstance(value, str):
            assert "testclient" not in value


def test_response_never_claims_verification(client: TestClient, db: Session):
    """No response field may claim that the QR code proves anything."""
    batch, _variant, _product = _seed_batch(db)
    service.record_batch_event(
        db,
        batch_id=batch.id,
        event_type="manufactured",
        public_text="This batch was manufactured.",
        actor_id=None,
    )
    db.commit()
    token = _issue_token(db, batch)

    resp = client.get(f"/api/v1/traceability/{token}")

    assert resp.status_code == 200
    assert "verified" not in resp.text.lower()
    assert "genuine" not in resp.text.lower()
    assert "authentic" not in resp.text.lower()


def test_public_endpoint_is_rate_limited(client: TestClient):
    """Exceeding the limit answers 429 instead of the lookup."""
    limit = traceability_rate_limiter.max_requests

    for _ in range(limit):
        resp = client.get("/api/v1/traceability/no-such-token")
        assert resp.status_code == 404

    resp = client.get("/api/v1/traceability/no-such-token")
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMIT_EXCEEDED"


def test_batch_events_are_shown_on_the_public_page_in_order(client: TestClient, db: Session):
    """The recorded events appear on the public page, oldest first."""
    batch, _variant, _product = _seed_batch(db)
    service.record_batch_event(
        db,
        batch_id=batch.id,
        event_type="manufactured",
        public_text="This batch was manufactured.",
        actor_id=None,
        event_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service.record_batch_event(
        db,
        batch_id=batch.id,
        event_type="quality_checked",
        public_text="This batch passed the quality check.",
        actor_id=None,
        event_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    service.record_batch_event(
        db,
        batch_id=batch.id,
        event_type="released",
        public_text="This batch was released for sale.",
        actor_id=None,
        event_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    db.commit()
    token = _issue_token(db, batch)

    resp = client.get(f"/api/v1/traceability/{token}")

    assert resp.status_code == 200
    events = resp.json()["events"]
    assert [event["event_type"] for event in events] == [
        "manufactured",
        "quality_checked",
        "released",
    ]
    assert [event["public_text"] for event in events] == [
        "This batch was manufactured.",
        "This batch passed the quality check.",
        "This batch was released for sale.",
    ]


def test_owner_can_issue_and_rotate_a_token(client: TestClient, db: Session, auth_headers):
    """The owner issues a token, and rotating it invalidates the previous link."""
    batch, _variant, _product = _seed_batch(db)
    headers = auth_headers("owner")

    issue_resp = client.post(f"/api/v1/traceability/batches/{batch.id}/token", headers=headers)
    assert issue_resp.status_code == 201
    first_token = issue_resp.json()["public_token"]
    assert first_token
    assert issue_resp.json()["rotated"] is False

    lookup = client.get(f"/api/v1/traceability/{first_token}")
    assert lookup.status_code == 200

    rotate_resp = client.post(
        f"/api/v1/traceability/batches/{batch.id}/token/rotate", headers=headers
    )
    assert rotate_resp.status_code == 200
    second_token = rotate_resp.json()["public_token"]
    assert second_token != first_token
    assert rotate_resp.json()["rotated"] is True

    # The old link stops resolving, the new one works.
    assert client.get(f"/api/v1/traceability/{first_token}").status_code == 404
    assert client.get(f"/api/v1/traceability/{second_token}").status_code == 200


def test_issued_token_appears_in_the_audit_log_without_the_token(
    client: TestClient, db: Session, auth_headers
):
    """The audit log records the action but never the secret itself."""
    from app.audit.models import AuditLog

    batch, _variant, _product = _seed_batch(db)
    headers = auth_headers("owner")

    resp = client.post(f"/api/v1/traceability/batches/{batch.id}/token", headers=headers)
    assert resp.status_code == 201
    token = resp.json()["public_token"]

    db.expire_all()
    entries = db.scalars(
        select(AuditLog).where(AuditLog.action == "TRACEABILITY_TOKEN_ISSUED")
    ).all()
    assert len(entries) == 1
    assert token not in str(entries[0].after)


def test_scan_log_is_owner_only(client: TestClient, db: Session, auth_headers):
    """Dealers and consumers may not read the scan log."""
    batch, _variant, _product = _seed_batch(db)
    token = _issue_token(db, batch)
    client.get(f"/api/v1/traceability/{token}")

    owner_headers = auth_headers("owner")
    resp = client.get("/api/v1/traceability/scans", headers=owner_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    for role in ("dealer", "consumer"):
        headers = auth_headers(role)
        assert client.get("/api/v1/traceability/scans", headers=headers).status_code == 403


def test_dealer_and_consumer_cannot_rotate_a_token(client: TestClient, db: Session, auth_headers):
    """Only owner and factory may issue or rotate a trace token."""
    batch, _variant, _product = _seed_batch(db)
    _issue_token(db, batch)

    for role in ("dealer", "consumer"):
        headers = auth_headers(role)
        rotate = client.post(
            f"/api/v1/traceability/batches/{batch.id}/token/rotate", headers=headers
        )
        assert rotate.status_code == 403
        issue = client.post(f"/api/v1/traceability/batches/{batch.id}/token", headers=headers)
        assert issue.status_code == 403


def test_public_text_may_not_claim_authenticity(db: Session):
    """The service rejects public text that claims the pack is genuine."""
    batch, _variant, _product = _seed_batch(db)

    with pytest.raises(AppError) as excinfo:
        service.record_batch_event(
            db,
            batch_id=batch.id,
            event_type="released",
            public_text="This pack is verified genuine.",
            actor_id=None,
        )
    assert excinfo.value.code == "INVALID_PUBLIC_TEXT"


def test_batch_events_are_recorded_through_the_api(client: TestClient, db: Session, auth_headers):
    """Factory staff can append an event, and the event shows up on the page."""
    batch, _variant, _product = _seed_batch(db)
    headers = auth_headers("factory")

    resp = client.post(
        f"/api/v1/traceability/batches/{batch.id}/events",
        headers=headers,
        json={"event_type": "dispatched", "public_text": "This batch was dispatched."},
    )
    assert resp.status_code == 201
    assert resp.json()["event_type"] == "dispatched"

    token = _issue_token(db, batch)
    lookup = client.get(f"/api/v1/traceability/{token}")
    assert lookup.status_code == 200
    events = lookup.json()["events"]
    assert [event["public_text"] for event in events] == ["This batch was dispatched."]
