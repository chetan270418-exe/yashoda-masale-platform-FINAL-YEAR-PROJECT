"""Tests for the procurement module (Milestone M3 / T-030).

Covers suppliers, raw materials, goods receipts (receipt + lines + material lots in one
transaction), validation, audit entries and role permissions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.models import User
from app.auth.security import create_access_token
from app.core.errors import AppError
from app.main import app as main_app
from app.procurement import router as procurement_router
from app.procurement import service
from app.procurement.models import MaterialLot, MaterialReceipt, MaterialReceiptLine
from app.procurement.schemas import (
    MaterialReceiptLineCreate,
    RawMaterialCreate,
    SupplierCreate,
)
from tests.conftest import _make_user


def _ensure_routers_registered() -> None:
    """Register the milestone routers when app/main.py does not include them yet.

    The parent integration registers both routers in app/main.py; until that happens the
    shared test client would not know the routes. The check keeps the registration
    idempotent so no route is ever added twice.
    """
    registered = {getattr(route, "path", "") for route in main_app.routes}
    if not any(path.startswith("/api/v1/procurement") for path in registered):
        main_app.include_router(procurement_router.router)


_ensure_routers_registered()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.name if user.role else ""})
    return {"Authorization": f"Bearer {token}"}


@dataclass
class _RawLine:
    """A receipt line built without the Pydantic schema (service-level validation test)."""

    raw_material_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    lot_code: str | None = None
    expiry_date: date | None = None


@pytest.fixture()
def factory_user(db: Session) -> User:
    return _make_user(db, email=f"factory-{uuid.uuid4().hex[:6]}@example.com", role_name="factory")


@pytest.fixture()
def owner_user(db: Session) -> User:
    return _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@example.com", role_name="owner")


@pytest.fixture()
def warehouse_user(db: Session) -> User:
    return _make_user(
        db, email=f"warehouse-{uuid.uuid4().hex[:6]}@example.com", role_name="warehouse"
    )


@pytest.fixture()
def dealer_user(db: Session) -> User:
    return _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")


@pytest.fixture()
def consumer_user(db: Session) -> User:
    return _make_user(
        db, email=f"consumer-{uuid.uuid4().hex[:6]}@example.com", role_name="consumer"
    )


@pytest.fixture()
def supplier(db: Session, factory_user: User):
    return service.create_supplier(
        db,
        SupplierCreate(code=f"SUP-{uuid.uuid4().hex[:8]}", name="Sangli Turmeric Growers"),
        actor_id=factory_user.id,
    )


@pytest.fixture()
def raw_material(db: Session, factory_user: User):
    return service.create_raw_material(
        db,
        RawMaterialCreate(code=f"RM-{uuid.uuid4().hex[:8]}", name="Turmeric Powder", unit="kg"),
        actor_id=factory_user.id,
    )


# ── Suppliers ────────────────────────────────────────────────────────────────


def test_create_supplier_persists_and_audits(db: Session, factory_user: User):
    """A supplier is stored with its details and the creation is audited."""
    created = service.create_supplier(
        db,
        SupplierCreate(
            code=f"SUP-{uuid.uuid4().hex[:8]}",
            name="Chilli Farmers Union",
            contact_email="contact@example.invalid",
            phone="+91-00000-00000",
        ),
        actor_id=factory_user.id,
    )

    assert created.id is not None
    assert created.is_active is True

    stored = db.scalar(select(type(created)).where(type(created).id == created.id))
    assert stored is not None
    assert stored.name == "Chilli Farmers Union"

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "suppliers",
            AuditLog.entity_id == created.id,
            AuditLog.action == "CREATE_SUPPLIER",
        )
    )
    assert audit is not None
    assert audit.actor_id == factory_user.id


def test_duplicate_supplier_code_is_rejected(db: Session, factory_user: User, supplier):
    """A duplicate supplier code is refused with a 409 error."""
    with pytest.raises(AppError) as excinfo:
        service.create_supplier(
            db,
            SupplierCreate(code=supplier.code, name="Another Supplier"),
            actor_id=factory_user.id,
        )
    assert excinfo.value.code == "DUPLICATE_SUPPLIER_CODE"
    assert excinfo.value.status_code == 409


def test_list_suppliers_hides_inactive_by_default(db: Session, factory_user: User, supplier):
    """Deactivated suppliers disappear from the default listing but stay in the full one."""
    service.deactivate_supplier(db, supplier.id, actor_id=factory_user.id)

    active = service.list_suppliers(db)
    assert all(item.id != supplier.id for item in active)

    everything = service.list_suppliers(db, include_inactive=True)
    assert any(item.id == supplier.id for item in everything)


def test_deactivate_supplier_blocks_new_receipts(
    db: Session, factory_user: User, supplier, raw_material
):
    """An inactive supplier cannot receive goods any more."""
    service.deactivate_supplier(db, supplier.id, actor_id=factory_user.id)

    with pytest.raises(AppError) as excinfo:
        service.record_material_receipt(
            db,
            supplier_id=supplier.id,
            lines=[
                MaterialReceiptLineCreate(
                    raw_material_id=raw_material.id,
                    quantity=Decimal("10.000"),
                    unit_cost=Decimal("100.00"),
                )
            ],
            received_on=date.today(),
            actor_id=factory_user.id,
        )
    assert excinfo.value.code == "SUPPLIER_INACTIVE"


# ── Raw materials ────────────────────────────────────────────────────────────


def test_create_raw_material_and_deactivate(db: Session, factory_user: User):
    """Raw materials are registered and can be deactivated."""
    material = service.create_raw_material(
        db,
        RawMaterialCreate(code=f"RM-{uuid.uuid4().hex[:8]}", name="Garam Masala Blend", unit="kg"),
        actor_id=factory_user.id,
    )
    assert material.is_active is True
    assert material.unit == "kg"

    service.deactivate_raw_material(db, material.id, actor_id=factory_user.id)
    assert db.get(type(material), material.id).is_active is False

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "raw_materials",
            AuditLog.entity_id == material.id,
            AuditLog.action == "DEACTIVATE_RAW_MATERIAL",
        )
    )
    assert audit is not None


def test_duplicate_raw_material_code_is_rejected(db: Session, factory_user: User, raw_material):
    """A duplicate raw material code is refused with a 409 error."""
    with pytest.raises(AppError) as excinfo:
        service.create_raw_material(
            db,
            RawMaterialCreate(code=raw_material.code, name="Duplicate", unit="kg"),
            actor_id=factory_user.id,
        )
    assert excinfo.value.code == "DUPLICATE_RAW_MATERIAL_CODE"


# ── Goods receipts ───────────────────────────────────────────────────────────


def test_record_material_receipt_creates_lines_lots_and_audit(
    db: Session, factory_user: User, supplier, raw_material
):
    """A receipt writes the receipt, its lines and the material lots in one transaction."""
    receipt = service.record_material_receipt(
        db,
        supplier_id=supplier.id,
        lines=[
            MaterialReceiptLineCreate(
                raw_material_id=raw_material.id,
                quantity=Decimal("250.000"),
                unit_cost=Decimal("180.50"),
            )
        ],
        received_on=date(2026, 10, 30),
        actor_id=factory_user.id,
        notes="First delivery of the season",
    )

    assert receipt.id is not None
    assert receipt.receipt_no.startswith("GRN-")
    assert receipt.supplier_id == supplier.id
    assert receipt.received_by == factory_user.id

    lines = db.scalars(
        select(MaterialReceiptLine).where(MaterialReceiptLine.receipt_id == receipt.id)
    ).all()
    assert len(lines) == 1
    assert lines[0].quantity == Decimal("250.000")
    assert lines[0].unit_cost == Decimal("180.50")

    lots = db.scalars(select(MaterialLot).where(MaterialLot.id == lines[0].material_lot_id)).all()
    assert len(lots) == 1
    assert lots[0].quantity == Decimal("250.000")
    assert lots[0].supplier_id == supplier.id
    assert lots[0].raw_material_id == raw_material.id
    assert lots[0].received_on == date(2026, 10, 30)

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.entity == "material_receipts",
            AuditLog.entity_id == receipt.id,
            AuditLog.action == "RECORD_MATERIAL_RECEIPT",
        )
    )
    assert audit is not None
    assert audit.after["line_count"] == 1


def test_record_material_receipt_rejects_zero_lines(db: Session, factory_user: User, supplier):
    """A receipt without lines is refused and nothing is written."""
    with pytest.raises(AppError) as excinfo:
        service.record_material_receipt(
            db,
            supplier_id=supplier.id,
            lines=[],
            received_on=date.today(),
            actor_id=factory_user.id,
        )
    assert excinfo.value.code == "EMPTY_MATERIAL_RECEIPT"

    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0
    assert db.scalar(select(func.count()).select_from(MaterialLot)) == 0


def test_record_material_receipt_rolls_back_on_invalid_quantity(
    db: Session, factory_user: User, supplier, raw_material
):
    """An invalid line rolls the whole receipt back: no receipt and no orphan lot.

    The line is built without the Pydantic schema on purpose: the service must guard the
    quantity itself, even when it is called directly.
    """
    invalid_line = _RawLine(
        raw_material_id=raw_material.id,
        quantity=Decimal("0.000"),
        unit_cost=Decimal("10.00"),
    )
    with pytest.raises(AppError) as excinfo:
        service.record_material_receipt(
            db,
            supplier_id=supplier.id,
            lines=[
                MaterialReceiptLineCreate(
                    raw_material_id=raw_material.id,
                    quantity=Decimal("250.000"),
                    unit_cost=Decimal("180.50"),
                ),
                invalid_line,
            ],
            received_on=date.today(),
            actor_id=factory_user.id,
        )
    assert excinfo.value.code == "INVALID_RECEIPT_QUANTITY"

    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0
    assert db.scalar(select(func.count()).select_from(MaterialReceiptLine)) == 0
    assert db.scalar(select(func.count()).select_from(MaterialLot)) == 0


def test_record_material_receipt_rejects_unknown_raw_material(
    db: Session, factory_user: User, supplier
):
    """An unknown raw material aborts the receipt without leaving rows behind."""
    with pytest.raises(AppError) as excinfo:
        service.record_material_receipt(
            db,
            supplier_id=supplier.id,
            lines=[
                MaterialReceiptLineCreate(
                    raw_material_id=uuid.uuid4(),
                    quantity=Decimal("5.000"),
                    unit_cost=Decimal("10.00"),
                )
            ],
            received_on=date.today(),
            actor_id=factory_user.id,
        )
    assert excinfo.value.code == "RAW_MATERIAL_NOT_FOUND"
    assert db.scalar(select(func.count()).select_from(MaterialReceipt)) == 0
    assert db.scalar(select(func.count()).select_from(MaterialLot)) == 0


def test_list_lots_filters_by_supplier_and_material(
    db: Session, factory_user: User, supplier, raw_material
):
    """Lots can be filtered by supplier and by raw material."""
    service.record_material_receipt(
        db,
        supplier_id=supplier.id,
        lines=[
            MaterialReceiptLineCreate(
                raw_material_id=raw_material.id,
                quantity=Decimal("10.000"),
                unit_cost=Decimal("99.00"),
            )
        ],
        received_on=date.today(),
        actor_id=factory_user.id,
    )

    assert len(service.list_lots(db, supplier_id=supplier.id)) == 1
    assert len(service.list_lots(db, raw_material_id=raw_material.id)) == 1
    assert service.list_lots(db, supplier_id=uuid.uuid4()) == []


# ── HTTP API ─────────────────────────────────────────────────────────────────


def test_api_records_a_receipt(client: TestClient, db: Session, factory_user: User):
    """The HTTP API creates a supplier, a material and a receipt."""
    supplier_resp = client.post(
        "/api/v1/procurement/suppliers",
        json={"code": f"SUP-API-{uuid.uuid4().hex[:8]}", "name": "API Supplier"},
        headers=_auth_header(factory_user),
    )
    assert supplier_resp.status_code == 201
    supplier_id = supplier_resp.json()["id"]

    material_resp = client.post(
        "/api/v1/procurement/raw-materials",
        json={"code": f"RM-API-{uuid.uuid4().hex[:8]}", "name": "API Material", "unit": "kg"},
        headers=_auth_header(factory_user),
    )
    assert material_resp.status_code == 201
    material_id = material_resp.json()["id"]

    receipt_resp = client.post(
        "/api/v1/procurement/receipts",
        json={
            "supplier_id": supplier_id,
            "received_on": "2026-10-30",
            "lines": [{"raw_material_id": material_id, "quantity": "12.500", "unit_cost": "45.25"}],
        },
        headers=_auth_header(factory_user),
    )
    assert receipt_resp.status_code == 201
    body = receipt_resp.json()
    assert body["receipt_no"].startswith("GRN-")
    assert len(body["lines"]) == 1
    assert body["lines"][0]["quantity"] == "12.500"

    lots_resp = client.get(
        "/api/v1/procurement/lots",
        headers=_auth_header(factory_user),
    )
    assert lots_resp.status_code == 200
    assert len(lots_resp.json()) == 1


def test_api_rejects_receipt_without_lines(client: TestClient, db: Session, factory_user: User):
    """A receipt without lines is rejected by the schema."""
    supplier_resp = client.post(
        "/api/v1/procurement/suppliers",
        json={"code": f"SUP-API-{uuid.uuid4().hex[:8]}", "name": "API Supplier"},
        headers=_auth_header(factory_user),
    )
    supplier_id = supplier_resp.json()["id"]

    resp = client.post(
        "/api/v1/procurement/receipts",
        json={"supplier_id": supplier_id, "received_on": "2026-10-30", "lines": []},
        headers=_auth_header(factory_user),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_api_permissions(client: TestClient, db: Session, dealer_user: User, consumer_user: User):
    """Dealers and consumers may not write; dealers may not read procurement records."""
    dealer_resp = client.post(
        "/api/v1/procurement/suppliers",
        json={"code": f"SUP-NOPE-{uuid.uuid4().hex[:8]}", "name": "Not allowed"},
        headers=_auth_header(dealer_user),
    )
    assert dealer_resp.status_code == 403
    assert dealer_resp.json()["code"] == "FORBIDDEN"

    consumer_resp = client.post(
        "/api/v1/procurement/receipts",
        json={
            "supplier_id": str(uuid.uuid4()),
            "received_on": "2026-10-30",
            "lines": [{"raw_material_id": str(uuid.uuid4()), "quantity": "1.000"}],
        },
        headers=_auth_header(consumer_user),
    )
    assert consumer_resp.status_code == 403

    read_resp = client.get("/api/v1/procurement/suppliers", headers=_auth_header(dealer_user))
    assert read_resp.status_code == 403

    anon_resp = client.get("/api/v1/procurement/suppliers")
    assert anon_resp.status_code == 401


def test_api_warehouse_can_read_but_not_write(
    client: TestClient, db: Session, warehouse_user: User
):
    """Warehouse staff may read procurement records but may not create them."""
    read_resp = client.get(
        "/api/v1/procurement/raw-materials", headers=_auth_header(warehouse_user)
    )
    assert read_resp.status_code == 200

    write_resp = client.post(
        "/api/v1/procurement/suppliers",
        json={"code": f"SUP-NOPE-{uuid.uuid4().hex[:8]}", "name": "Not allowed"},
        headers=_auth_header(warehouse_user),
    )
    assert write_resp.status_code == 403


def test_api_owner_can_write_procurement_records(client: TestClient, db: Session, owner_user: User):
    """The owner role may create procurement records."""
    resp = client.post(
        "/api/v1/procurement/raw-materials",
        json={"code": f"RM-OWNER-{uuid.uuid4().hex[:8]}", "name": "Owner Material", "unit": "kg"},
        headers=_auth_header(owner_user),
    )
    assert resp.status_code == 201
    assert resp.json()["is_active"] is True


def test_openapi_documents_every_procurement_endpoint(client: TestClient, db: Session):
    """Every procurement endpoint appears in the OpenAPI schema with its models."""
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]

    expected = {
        "/api/v1/procurement/suppliers": {"post": True, "get": False},
        "/api/v1/procurement/suppliers/{supplier_id}/deactivate": {"post": False},
        "/api/v1/procurement/raw-materials": {"post": True, "get": False},
        "/api/v1/procurement/raw-materials/{raw_material_id}/deactivate": {"post": False},
        "/api/v1/procurement/receipts": {"post": True, "get": False},
        "/api/v1/procurement/lots": {"get": False},
    }
    for path, methods in expected.items():
        assert path in paths, f"{path} is missing from the OpenAPI schema"
        for method, has_body in methods.items():
            operation = paths[path][method]
            assert operation["responses"], f"{method.upper()} {path} has no response model"
            if has_body:
                assert "requestBody" in operation, f"{method.upper()} {path} has no request model"
