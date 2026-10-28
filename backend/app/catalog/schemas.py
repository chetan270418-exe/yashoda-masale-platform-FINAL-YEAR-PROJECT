"""Catalog Pydantic schemas for request/response validation."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# ── Categories ────────────────────────────────────────────────────────────────


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(None, max_length=500)


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None


# ── Variants ──────────────────────────────────────────────────────────────────


class VariantCreate(BaseModel):
    sku: str = Field(..., min_length=3, max_length=50)
    pack_size_g: int = Field(..., gt=0, description="Pack size in grams")
    image_url: str | None = Field(None, max_length=500)
    is_active: bool = True


class VariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    pack_size_g: int
    image_url: str | None = None
    is_active: bool
    price: Decimal | None = Field(
        None, description="Resolved unit price for caller's role (FR-CAT-03)"
    )


# ── Products ──────────────────────────────────────────────────────────────────


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    category_id: uuid.UUID
    description: str | None = Field(None, max_length=1000)
    is_active: bool = True
    variants: list[VariantCreate] = []


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    category_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=1000)
    is_active: bool | None = None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category_id: uuid.UUID
    category_name: str | None = None
    description: str | None = None
    is_active: bool
    created_at: datetime
    variants: list[VariantRead] = []


# ── Price Lists ───────────────────────────────────────────────────────────────


class PriceListCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    description: str | None = Field(None, max_length=200)


class PriceListRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None


class PriceListItemCreate(BaseModel):
    price_list_id: uuid.UUID | None = None
    variant_id: uuid.UUID
    price: Decimal = Field(..., gt=0, decimal_places=2, description="Fixed precision decimal (B12)")
    valid_from: datetime | None = None


class PriceListItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    price_list_id: uuid.UUID
    variant_id: uuid.UUID
    price: Decimal
    valid_from: datetime
