"""Tests for Invoices, Payments, Shipments and Logistics Events (Milestone M5 / T-052).

Enforces:
- Invariant B8 & B12: Decimal money arithmetic for invoices.
- Rule 91 & D-009: Payments are simulated, no card data stored.
- RBAC: Customers see own invoices and shipments; staff manage lifecycle.
- Auto-generation: Dispatched orders automatically issue an invoice and shipment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.inventory.models import InventoryBalance, ProductionBatch, Warehouse
from app.orders.models import Invoice, Shipment, ShipmentEvent
from tests.conftest import _make_user


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def t052_setup(db: Session):
    cat = Category(id=uuid.uuid4(), name=f"Spices-T052-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(id=uuid.uuid4(), name="Garam Masala T052", category_id=cat.id, is_active=True)
    db.add(prod)
    db.flush()

    v1 = ProductVariant(
        id=uuid.uuid4(),
        product_id=prod.id,
        sku=f"GM-250G-{uuid.uuid4().hex[:4]}",
        pack_size_g=250,
        is_active=True,
    )
    db.add(v1)
    db.flush()

    pl = db.scalar(select(PriceList).where(PriceList.name == "DEALER"))
    if not pl:
        pl = PriceList(id=uuid.uuid4(), name="DEALER")
        db.add(pl)
        db.flush()

    now = datetime.now(UTC)
    price_item = PriceListItem(
        id=uuid.uuid4(),
        price_list_id=pl.id,
        variant_id=v1.id,
        price=Decimal("120.00"),
        valid_from=now - timedelta(days=1),
    )
    db.add(price_item)
    db.flush()

    wh = Warehouse(
        id=uuid.uuid4(),
        code=f"WH-T052-{uuid.uuid4().hex[:6]}",
        name="T052 Warehouse",
        is_primary=True,
        is_active=True,
    )
    db.add(wh)
    db.flush()

    today = date.today()
    batch = ProductionBatch(
        id=uuid.uuid4(),
        batch_code=f"BATCH-T052-{uuid.uuid4().hex[:6]}",
        variant_id=v1.id,
        status="released",
        manufactured_at=today - timedelta(days=5),
        expiry_date=today + timedelta(days=240),
    )
    db.add(batch)
    db.flush()

    bal = InventoryBalance(
        id=uuid.uuid4(),
        warehouse_id=wh.id,
        batch_id=batch.id,
        on_hand=Decimal("500.000"),
        reserved=Decimal("0.000"),
    )
    db.add(bal)
    db.commit()

    return {"variant": v1, "warehouse": wh, "batch": batch, "balance": bal}


def test_create_invoice_for_order_and_record_payment(client: TestClient, db: Session, t052_setup):
    """Staff can issue an invoice for an order, and customer can make simulated payment."""
    dealer = _make_user(db, email=f"dealer-pay-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-inv-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    var = t052_setup["variant"]

    # 1. Dealer creates order for 10 units @ 120.00 = 1200.00
    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var.id), "quantity": "10.000"}]},
        headers=_auth_header(dealer),
    )
    assert create_resp.status_code == 201
    order_id = create_resp.json()["id"]

    # 2. Staff issues invoice
    inv_resp = client.post(
        f"/api/v1/orders/{order_id}/invoices",
        json={"notes": "Standard dealer billing 30 days credit"},
        headers=_auth_header(owner),
    )
    assert inv_resp.status_code == 201
    inv_data = inv_resp.json()
    assert inv_data["total_amount"] == "1200.00"
    assert inv_data["payment_status"] == "unpaid"
    assert inv_data["currency"] == "INR"
    assert inv_data["invoice_number"].startswith("INV-")
    invoice_id = inv_data["id"]

    # 3. Customer views own invoice
    get_resp = client.get(f"/api/v1/orders/{order_id}/invoices", headers=_auth_header(dealer))
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == invoice_id

    # 4. Underpayment is rejected
    underpay_resp = client.post(
        f"/api/v1/orders/invoices/{invoice_id}/pay",
        json={"amount": "1000.00", "payment_method": "simulated_upi"},
        headers=_auth_header(dealer),
    )
    assert underpay_resp.status_code == 400
    assert underpay_resp.json()["code"] == "INSUFFICIENT_PAYMENT"

    # 5. Full simulated payment succeeds
    pay_resp = client.post(
        f"/api/v1/orders/invoices/{invoice_id}/pay",
        json={
            "amount": "1200.00",
            "payment_method": "simulated_upi",
            "payment_reference": "UPI-TXN-88129304",
            "notes": "Paid via GooglePay simulated gateway",
        },
        headers=_auth_header(dealer),
    )
    assert pay_resp.status_code == 200
    assert pay_resp.json()["payment_status"] == "paid"
    assert pay_resp.json()["payment_method"] == "simulated_upi"
    assert pay_resp.json()["payment_reference"] == "UPI-TXN-88129304"
    assert pay_resp.json()["paid_at"] is not None

    # 6. Paying already paid invoice is rejected
    double_pay = client.post(
        f"/api/v1/orders/invoices/{invoice_id}/pay",
        json={"amount": "1200.00"},
        headers=_auth_header(dealer),
    )
    assert double_pay.status_code == 400
    assert double_pay.json()["code"] == "ALREADY_PAID"


def test_invoice_rbac_customer_isolation(client: TestClient, db: Session, t052_setup):
    """Customer A cannot view or pay Customer B's invoice (HTTP 403)."""
    c_a = _make_user(db, email=f"cust-a-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    c_b = _make_user(db, email=f"cust-b-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-sec-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    var = t052_setup["variant"]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var.id), "quantity": "5.000"}]},
        headers=_auth_header(c_a),
    )
    order_id = create_resp.json()["id"]

    inv_resp = client.post(f"/api/v1/orders/{order_id}/invoices", headers=_auth_header(owner))
    invoice_id = inv_resp.json()["id"]

    # Customer B attempts to get Customer A's invoice via order endpoint -> 403
    resp1 = client.get(f"/api/v1/orders/{order_id}/invoices", headers=_auth_header(c_b))
    assert resp1.status_code == 403

    # Customer B attempts to get Customer A's invoice by ID -> 403
    resp2 = client.get(f"/api/v1/orders/invoices/{invoice_id}", headers=_auth_header(c_b))
    assert resp2.status_code == 403

    # Customer B attempts to pay Customer A's invoice -> 403
    resp3 = client.post(
        f"/api/v1/orders/invoices/{invoice_id}/pay",
        json={"amount": "600.00"},
        headers=_auth_header(c_b),
    )
    assert resp3.status_code == 403


def test_dispatch_auto_creates_invoice_and_shipment(client: TestClient, db: Session, t052_setup):
    """Dispatching an order with carrier auto-creates invoice and shipment records."""
    dealer = _make_user(
        db, email=f"dealer-disp-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer"
    )
    owner = _make_user(db, email=f"owner-disp-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    var = t052_setup["variant"]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var.id), "quantity": "8.000"}]},
        headers=_auth_header(dealer),
    )
    order_id = create_resp.json()["id"]

    client.post(f"/api/v1/orders/{order_id}/reserve", headers=_auth_header(owner))
    client.post(f"/api/v1/orders/{order_id}/pack", headers=_auth_header(owner))

    # Dispatch with carrier and tracking
    disp_resp = client.post(
        f"/api/v1/orders/{order_id}/dispatch",
        json={"carrier": "Delhivery Express", "tracking_ref": "DEL-99120391"},
        headers=_auth_header(owner),
    )
    assert disp_resp.status_code == 200

    # Verify Invoice was automatically issued
    inv = db.scalar(select(Invoice).where(Invoice.order_id == uuid.UUID(order_id)))
    assert inv is not None
    assert inv.total_amount == Decimal("960.00")
    assert inv.payment_status == "unpaid"

    # Verify Shipment was automatically created
    shipments = list(
        db.scalars(select(Shipment).where(Shipment.order_id == uuid.UUID(order_id))).all()
    )
    assert len(shipments) == 1
    shipment = shipments[0]
    assert shipment.carrier == "Delhivery Express"
    assert shipment.tracking_ref == "DEL-99120391"
    assert shipment.status == "in_transit"
    assert shipment.dispatched_at is not None

    # Check timeline event
    events = list(
        db.scalars(select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment.id)).all()
    )
    assert len(events) >= 1
    assert events[0].event_type == "dispatched"


def test_shipment_lifecycle_and_tracking_events(client: TestClient, db: Session, t052_setup):
    """Shipment creation, status advancement, and checkpoint events tracking."""
    dealer = _make_user(
        db, email=f"dealer-ship-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer"
    )
    owner = _make_user(db, email=f"owner-ship-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")
    var = t052_setup["variant"]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var.id), "quantity": "4.000"}]},
        headers=_auth_header(dealer),
    )
    order_id = create_resp.json()["id"]

    # 1. Staff creates shipment
    ship_create_resp = client.post(
        f"/api/v1/orders/{order_id}/shipments",
        json={"carrier": "BlueDart", "tracking_ref": "BD-77123910", "notes": "Fragile spice packs"},
        headers=_auth_header(owner),
    )
    assert ship_create_resp.status_code == 201
    ship_data = ship_create_resp.json()
    shipment_id = ship_data["id"]
    assert ship_data["status"] == "manifested"
    assert len(ship_data["events"]) == 1

    # 2. Staff advances status to in_transit
    upd_resp = client.patch(
        f"/api/v1/orders/shipments/{shipment_id}/status",
        json={
            "status": "in_transit",
            "location": "Pune Logistics Hub",
            "description": "Departed Pune facility",
        },
        headers=_auth_header(owner),
    )
    assert upd_resp.status_code == 200
    assert upd_resp.json()["status"] == "in_transit"
    assert len(upd_resp.json()["events"]) == 2

    # 3. Staff adds custom checkpoint event
    event_resp = client.post(
        f"/api/v1/orders/shipments/{shipment_id}/events",
        json={
            "event_type": "out_for_delivery",
            "location": "Mumbai Local Depot",
            "description": "Out for delivery with courier agent Rajesh",
        },
        headers=_auth_header(owner),
    )
    assert event_resp.status_code == 201
    assert event_resp.json()["event_type"] == "out_for_delivery"

    # 4. Dealer views own shipment and events timeline
    view_resp = client.get(f"/api/v1/orders/shipments/{shipment_id}", headers=_auth_header(dealer))
    assert view_resp.status_code == 200
    timeline = view_resp.json()["events"]
    assert len(timeline) >= 3
    event_types = [e["event_type"] for e in timeline]
    assert "manifested" in event_types
    assert "in_transit" in event_types
    assert "out_for_delivery" in event_types

    # 5. Advance to delivered
    deliv_resp = client.patch(
        f"/api/v1/orders/shipments/{shipment_id}/status",
        json={
            "status": "delivered",
            "location": "Dealer Store",
            "description": "Delivered to shop owner",
        },
        headers=_auth_header(owner),
    )
    assert deliv_resp.status_code == 200
    assert deliv_resp.json()["status"] == "delivered"
    assert deliv_resp.json()["delivered_at"] is not None
