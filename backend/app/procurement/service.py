"""Procurement service: suppliers, raw materials, lots and goods receipts (T-030).

Rules honoured:
- Rule 41 (database rules): all writes go through the ORM and are committed in one
  transaction; a receipt, its lines and its material lots are written together.
- Rule 43 (audit): supplier, material and receipt changes are audited.
- Invariant B12: quantities and money are Decimal, never float.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_action
from app.core.errors import AppError
from app.procurement.models import (
    MaterialLot,
    MaterialReceipt,
    MaterialReceiptLine,
    RawMaterial,
    Supplier,
)
from app.procurement.schemas import (
    MaterialReceiptLineCreate,
    MaterialReceiptLineRead,
    MaterialReceiptRead,
    RawMaterialCreate,
    SupplierCreate,
)


def generate_receipt_no() -> str:
    """Generate a human-readable unique goods-receipt number."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"GRN-{stamp}-{uuid.uuid4().hex[:8].upper()}"


def _as_decimal(value: Decimal | int | float | str, field: str) -> Decimal:
    """Convert an incoming number to Decimal without ever using binary floats (B12)."""
    try:
        return Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive
        raise AppError(
            "INVALID_NUMERIC_VALUE",
            f"Invalid numeric value for {field}: {value!r}.",
            400,
        ) from exc


def create_supplier(db: Session, data: SupplierCreate, actor_id: uuid.UUID) -> Supplier:
    """Register a raw-material supplier and audit the creation."""
    existing = db.scalar(select(Supplier).where(Supplier.code == data.code))
    if existing:
        raise AppError(
            "DUPLICATE_SUPPLIER_CODE", f"Supplier code '{data.code}' already exists.", 409
        )

    supplier = Supplier(
        id=uuid.uuid4(),
        code=data.code,
        name=data.name,
        contact_email=data.contact_email,
        phone=data.phone,
        address=data.address,
        is_active=True,
    )
    try:
        db.add(supplier)
        db.flush()
        log_action(
            db,
            actor_id=actor_id,
            action="CREATE_SUPPLIER",
            entity="suppliers",
            entity_id=supplier.id,
            after={
                "code": supplier.code,
                "name": supplier.name,
                "contact_email": supplier.contact_email,
                "is_active": supplier.is_active,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_SUPPLIER_CODE", f"Supplier code '{data.code}' already exists.", 409
        ) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(supplier)
    return supplier


def list_suppliers(db: Session, include_inactive: bool = False) -> list[Supplier]:
    """List suppliers, active ones first unless inactive ones are requested."""
    stmt = select(Supplier).order_by(Supplier.code.asc())
    if not include_inactive:
        stmt = stmt.where(Supplier.is_active.is_(True))
    return list(db.scalars(stmt).all())


def deactivate_supplier(db: Session, supplier_id: uuid.UUID, actor_id: uuid.UUID) -> Supplier:
    """Deactivate a supplier so that it can no longer receive goods."""
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise AppError("SUPPLIER_NOT_FOUND", f"Supplier '{supplier_id}' was not found.", 404)

    before = {"is_active": supplier.is_active}
    supplier.is_active = False
    log_action(
        db,
        actor_id=actor_id,
        action="DEACTIVATE_SUPPLIER",
        entity="suppliers",
        entity_id=supplier.id,
        before=before,
        after={"is_active": supplier.is_active},
    )
    db.commit()
    db.refresh(supplier)
    return supplier


def create_raw_material(db: Session, data: RawMaterialCreate, actor_id: uuid.UUID) -> RawMaterial:
    """Register a raw material and audit the creation."""
    existing = db.scalar(select(RawMaterial).where(RawMaterial.code == data.code))
    if existing:
        raise AppError(
            "DUPLICATE_RAW_MATERIAL_CODE", f"Raw material code '{data.code}' already exists.", 409
        )

    material = RawMaterial(
        id=uuid.uuid4(),
        code=data.code,
        name=data.name,
        unit=data.unit,
        is_active=True,
    )
    try:
        db.add(material)
        db.flush()
        log_action(
            db,
            actor_id=actor_id,
            action="CREATE_RAW_MATERIAL",
            entity="raw_materials",
            entity_id=material.id,
            after={"code": material.code, "name": material.name, "unit": material.unit},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_RAW_MATERIAL_CODE",
            f"Raw material code '{data.code}' already exists.",
            409,
        ) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(material)
    return material


def list_raw_materials(db: Session, include_inactive: bool = False) -> list[RawMaterial]:
    """List raw materials, active ones first unless inactive ones are requested."""
    stmt = select(RawMaterial).order_by(RawMaterial.code.asc())
    if not include_inactive:
        stmt = stmt.where(RawMaterial.is_active.is_(True))
    return list(db.scalars(stmt).all())


def deactivate_raw_material(
    db: Session, raw_material_id: uuid.UUID, actor_id: uuid.UUID
) -> RawMaterial:
    """Deactivate a raw material so that it can no longer be received."""
    material = db.get(RawMaterial, raw_material_id)
    if material is None:
        raise AppError(
            "RAW_MATERIAL_NOT_FOUND", f"Raw material '{raw_material_id}' was not found.", 404
        )

    before = {"is_active": material.is_active}
    material.is_active = False
    log_action(
        db,
        actor_id=actor_id,
        action="DEACTIVATE_RAW_MATERIAL",
        entity="raw_materials",
        entity_id=material.id,
        before=before,
        after={"is_active": material.is_active},
    )
    db.commit()
    db.refresh(material)
    return material


def record_material_receipt(
    db: Session,
    supplier_id: uuid.UUID,
    lines: Sequence[MaterialReceiptLineCreate],
    received_on: date,
    actor_id: uuid.UUID,
    notes: str | None = None,
) -> MaterialReceipt:
    """Record a goods receipt: the receipt, its lines and the new material lots.

    Everything is written inside ONE transaction: if any line is invalid the whole
    receipt is rolled back and nothing is persisted (no orphan lots, no half receipt).

    Validation:
    - the receipt must have at least one line;
    - the supplier must exist and be active;
    - every raw material must exist and be active;
    - every quantity must be strictly greater than zero and every unit cost non-negative.

    A receipt line creates exactly one material lot, so the lot carries the received
    quantity, the unit cost and the optional expiry date.
    """
    if not lines:
        raise AppError(
            "EMPTY_MATERIAL_RECEIPT", "A material receipt must contain at least one line.", 400
        )

    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise AppError("SUPPLIER_NOT_FOUND", f"Supplier '{supplier_id}' was not found.", 404)
    if not supplier.is_active:
        raise AppError(
            "SUPPLIER_INACTIVE",
            f"Supplier '{supplier.code}' is inactive and cannot receive goods.",
            400,
        )

    receipt = MaterialReceipt(
        id=uuid.uuid4(),
        receipt_no=generate_receipt_no(),
        supplier_id=supplier.id,
        received_on=received_on,
        received_by=actor_id,
        notes=notes,
    )

    try:
        db.add(receipt)
        db.flush()

        total_quantity = Decimal("0")
        for index, line in enumerate(lines, start=1):
            quantity = _as_decimal(line.quantity, "quantity")
            unit_cost = _as_decimal(line.unit_cost, "unit_cost")
            if quantity <= Decimal("0"):
                raise AppError(
                    "INVALID_RECEIPT_QUANTITY",
                    "Receipt line quantity must be greater than zero.",
                    400,
                )
            if unit_cost < Decimal("0"):
                raise AppError(
                    "INVALID_RECEIPT_COST", "Receipt line unit cost cannot be negative.", 400
                )

            material = db.get(RawMaterial, line.raw_material_id)
            if material is None:
                raise AppError(
                    "RAW_MATERIAL_NOT_FOUND",
                    f"Raw material '{line.raw_material_id}' was not found.",
                    404,
                )
            if not material.is_active:
                raise AppError(
                    "RAW_MATERIAL_INACTIVE",
                    f"Raw material '{material.code}' is inactive and cannot be received.",
                    400,
                )

            lot = MaterialLot(
                id=uuid.uuid4(),
                lot_code=line.lot_code or f"{receipt.receipt_no}-{index:02d}",
                supplier_id=supplier.id,
                raw_material_id=material.id,
                quantity=quantity,
                unit_cost=unit_cost,
                received_on=received_on,
                expiry_date=line.expiry_date,
                is_active=True,
            )
            db.add(lot)
            db.flush()

            db.add(
                MaterialReceiptLine(
                    id=uuid.uuid4(),
                    receipt_id=receipt.id,
                    material_lot_id=lot.id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                )
            )
            total_quantity += quantity

        log_action(
            db,
            actor_id=actor_id,
            action="RECORD_MATERIAL_RECEIPT",
            entity="material_receipts",
            entity_id=receipt.id,
            after={
                "receipt_no": receipt.receipt_no,
                "supplier_id": str(supplier.id),
                "supplier_code": supplier.code,
                "received_on": received_on.isoformat(),
                "line_count": len(lines),
                "total_quantity": str(total_quantity),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_MATERIAL_LOT_CODE",
            "A material lot with the same lot code already exists.",
            409,
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(receipt)
    return receipt


def list_receipts(
    db: Session,
    supplier_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MaterialReceipt]:
    """List goods receipts, newest first, optionally filtered by supplier."""
    stmt = select(MaterialReceipt).order_by(MaterialReceipt.received_on.desc())
    if supplier_id is not None:
        stmt = stmt.where(MaterialReceipt.supplier_id == supplier_id)
    stmt = stmt.limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


def list_lots(
    db: Session,
    supplier_id: uuid.UUID | None = None,
    raw_material_id: uuid.UUID | None = None,
    is_active: bool | None = None,
) -> list[MaterialLot]:
    """List material lots with optional supplier and raw-material filters."""
    stmt = select(MaterialLot).order_by(MaterialLot.received_on.desc(), MaterialLot.lot_code.asc())
    if supplier_id is not None:
        stmt = stmt.where(MaterialLot.supplier_id == supplier_id)
    if raw_material_id is not None:
        stmt = stmt.where(MaterialLot.raw_material_id == raw_material_id)
    if is_active is not None:
        stmt = stmt.where(MaterialLot.is_active.is_(is_active))
    return list(db.scalars(stmt).all())


def build_receipt_read(receipt: MaterialReceipt) -> MaterialReceiptRead:
    """Build the API read model of a goods receipt, including the lot code of every line."""
    return MaterialReceiptRead(
        id=receipt.id,
        receipt_no=receipt.receipt_no,
        supplier_id=receipt.supplier_id,
        supplier_name=receipt.supplier.name if receipt.supplier else None,
        received_on=receipt.received_on,
        received_by=receipt.received_by,
        notes=receipt.notes,
        created_at=receipt.created_at,
        lines=[
            MaterialReceiptLineRead(
                id=line.id,
                receipt_id=line.receipt_id,
                material_lot_id=line.material_lot_id,
                lot_code=line.material_lot.lot_code if line.material_lot else None,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
            )
            for line in receipt.lines
        ],
    )
