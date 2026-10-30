"""Procurement HTTP endpoints under /api/v1/procurement (Milestone M3 / T-030).

RBAC policy:
- owner and factory may write (suppliers, raw materials, receipts);
- owner, factory and warehouse may read.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.auth.models import User
from app.core.db import get_db
from app.procurement import service
from app.procurement.schemas import (
    MaterialLotRead,
    MaterialReceiptCreate,
    MaterialReceiptRead,
    RawMaterialCreate,
    RawMaterialRead,
    SupplierCreate,
    SupplierRead,
)

router = APIRouter(prefix="/api/v1/procurement", tags=["procurement"])

# Singletons to prevent FastAPI dependency re-instantiation / Ruff B008 warnings
require_procurement_writer = require_roles("owner", "factory")
require_procurement_reader = require_roles("owner", "factory", "warehouse")


@router.post(
    "/suppliers",
    response_model=SupplierRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a raw-material supplier",
)
def create_supplier(
    payload: SupplierCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_procurement_writer)],
) -> SupplierRead:
    """Register a supplier. Owner and factory only."""
    supplier = service.create_supplier(db, payload, actor_id=current_user.id)
    return SupplierRead.model_validate(supplier)


@router.get(
    "/suppliers",
    response_model=list[SupplierRead],
    summary="List suppliers",
)
def list_suppliers(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_procurement_reader)],
    include_inactive: bool = Query(False, description="Include deactivated suppliers"),
) -> list[SupplierRead]:
    """List suppliers. Owner, factory and warehouse may read."""
    suppliers = service.list_suppliers(db, include_inactive=include_inactive)
    return [SupplierRead.model_validate(s) for s in suppliers]


@router.post(
    "/suppliers/{supplier_id}/deactivate",
    response_model=SupplierRead,
    summary="Deactivate a supplier",
)
def deactivate_supplier(
    supplier_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_procurement_writer)],
) -> SupplierRead:
    """Deactivate a supplier so it can no longer receive goods. Owner and factory only."""
    supplier = service.deactivate_supplier(db, supplier_id, actor_id=current_user.id)
    return SupplierRead.model_validate(supplier)


@router.post(
    "/raw-materials",
    response_model=RawMaterialRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a raw material",
)
def create_raw_material(
    payload: RawMaterialCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_procurement_writer)],
) -> RawMaterialRead:
    """Register a raw material. Owner and factory only."""
    material = service.create_raw_material(db, payload, actor_id=current_user.id)
    return RawMaterialRead.model_validate(material)


@router.get(
    "/raw-materials",
    response_model=list[RawMaterialRead],
    summary="List raw materials",
)
def list_raw_materials(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_procurement_reader)],
    include_inactive: bool = Query(False, description="Include deactivated raw materials"),
) -> list[RawMaterialRead]:
    """List raw materials. Owner, factory and warehouse may read."""
    materials = service.list_raw_materials(db, include_inactive=include_inactive)
    return [RawMaterialRead.model_validate(m) for m in materials]


@router.post(
    "/raw-materials/{raw_material_id}/deactivate",
    response_model=RawMaterialRead,
    summary="Deactivate a raw material",
)
def deactivate_raw_material(
    raw_material_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_procurement_writer)],
) -> RawMaterialRead:
    """Deactivate a raw material so it can no longer be received. Owner and factory only."""
    material = service.deactivate_raw_material(db, raw_material_id, actor_id=current_user.id)
    return RawMaterialRead.model_validate(material)


@router.post(
    "/receipts",
    response_model=MaterialReceiptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a goods receipt (creates material lots)",
)
def record_material_receipt(
    payload: MaterialReceiptCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_procurement_writer)],
) -> MaterialReceiptRead:
    """Record a goods receipt with its lines and the material lots they create.

    The receipt, its lines and the new lots are written in ONE transaction.
    Owner and factory only.
    """
    receipt = service.record_material_receipt(
        db,
        supplier_id=payload.supplier_id,
        lines=payload.lines,
        received_on=payload.received_on,
        actor_id=current_user.id,
        notes=payload.notes,
    )
    return service.build_receipt_read(receipt)


@router.get(
    "/receipts",
    response_model=list[MaterialReceiptRead],
    summary="List goods receipts",
)
def list_material_receipts(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_procurement_reader)],
    supplier_id: uuid.UUID | None = Query(None, description="Filter by supplier UUID"),
    limit: int = Query(50, ge=1, le=200, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
) -> list[MaterialReceiptRead]:
    """List goods receipts, newest first. Owner, factory and warehouse may read."""
    receipts = service.list_receipts(db, supplier_id=supplier_id, limit=limit, offset=offset)
    return [service.build_receipt_read(r) for r in receipts]


@router.get(
    "/lots",
    response_model=list[MaterialLotRead],
    summary="List received material lots",
)
def list_material_lots(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[User, Depends(require_procurement_reader)],
    supplier_id: uuid.UUID | None = Query(None, description="Filter by supplier UUID"),
    raw_material_id: uuid.UUID | None = Query(None, description="Filter by raw material UUID"),
    is_active: bool | None = Query(None, description="Filter by active flag"),
) -> list[MaterialLotRead]:
    """List material lots with optional supplier and raw-material filters."""
    lots = service.list_lots(
        db,
        supplier_id=supplier_id,
        raw_material_id=raw_material_id,
        is_active=is_active,
    )
    return [MaterialLotRead.model_validate(lot) for lot in lots]
