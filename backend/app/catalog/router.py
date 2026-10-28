"""Catalog HTTP endpoints under /api/v1/categories and /api/v1/products."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_optional_current_user, require_owner
from app.auth.models import User
from app.catalog import service
from app.catalog.schemas import (
    CategoryCreate,
    CategoryRead,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    VariantCreate,
    VariantRead,
)
from app.core.db import get_db

router = APIRouter(prefix="/api/v1", tags=["catalog"])


class PriceSetRequest(BaseModel):
    price: Decimal = Field(..., gt=0, decimal_places=2, description="Price in INR")


# ── Categories ────────────────────────────────────────────────────────────────


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryRead]:
    """Public list of product categories."""
    return service.list_categories(db)


@router.post("/categories", response_model=CategoryRead, status_code=201)
def create_category(
    body: CategoryCreate,
    db: Session = Depends(get_db),
    _owner: User = Depends(require_owner),
) -> CategoryRead:
    """Create a new product category (owner only)."""
    return service.create_category(db, body)


# ── Products ──────────────────────────────────────────────────────────────────


@router.get("/products", response_model=list[ProductRead])
def list_products(
    category_id: uuid.UUID | None = Query(None, description="Filter by category ID"),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
) -> list[ProductRead]:
    """List products with prices resolved specifically for the caller's role (B9, FR-CAT-03).

    - Dealers receive dealer prices.
    - Distributors receive distributor prices.
    - Consumers or anonymous requests receive MRP.
    """
    role_name = user.role.name if user else None
    return service.get_products_for_user(db, role_name=role_name, category_id=category_id)


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
) -> ProductRead:
    """Get single product with prices resolved for the caller's role."""
    role_name = user.role.name if user else None
    return service.get_product_for_user(db, product_id=product_id, role_name=role_name)


@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(
    body: ProductCreate,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
) -> ProductRead:
    """Create a new product with optional initial variants (owner only)."""
    return service.create_product(db, body, actor_id=owner.id)


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
) -> ProductRead:
    """Update product details or active state (owner only)."""
    return service.update_product(db, product_id, body, actor_id=owner.id)


@router.post("/products/{product_id}/variants", response_model=VariantRead, status_code=201)
def add_variant(
    product_id: uuid.UUID,
    body: VariantCreate,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
) -> VariantRead:
    """Add a new pack-size SKU variant to an existing product (owner only)."""
    return service.add_variant_to_product(db, product_id, body, actor_id=owner.id)


@router.put("/products/variants/{variant_id}/prices/{price_list_name}", status_code=200)
def set_variant_price(
    variant_id: uuid.UUID,
    price_list_name: str,
    body: PriceSetRequest,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
) -> dict[str, str]:
    """Set variant price in a tier (MRP, DEALER, DISTRIBUTOR) (owner only, B8)."""
    service.set_variant_price(
        db,
        variant_id=variant_id,
        price_list_name=price_list_name.upper(),
        price=body.price,
        actor_id=owner.id,
    )
    return {"status": "ok", "message": f"Price for {price_list_name.upper()} updated."}
