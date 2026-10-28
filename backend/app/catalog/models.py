"""Catalog ORM models: Category, Product, ProductVariant, PriceList, PriceListItem.

Implementation matches spec-pack/implementation.md section 2.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Category(Base):
    """Product category (e.g., Pure Spices, Blended Masalas)."""

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    products: Mapped[list["Product"]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<Category {self.name}>"


class Product(Base):
    """Spices and blends (e.g., Garam Masala, Turmeric Powder)."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    category: Mapped[Category] = relationship(back_populates="products", lazy="joined")
    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Product {self.name}>"


class ProductVariant(Base):
    """Stock-keeping unit (SKU) by pack size (e.g., Garam Masala 100g, 500g)."""

    __tablename__ = "product_variants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    pack_size_g: Mapped[int] = mapped_column(Integer, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    product: Mapped[Product] = relationship(back_populates="variants")
    price_items: Mapped[list["PriceListItem"]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ProductVariant {self.sku} ({self.pack_size_g}g)>"


class PriceList(Base):
    """Price tier (MRP, DEALER, DISTRIBUTOR)."""

    __tablename__ = "price_lists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    items: Mapped[list["PriceListItem"]] = relationship(back_populates="price_list")

    def __repr__(self) -> str:
        return f"<PriceList {self.name}>"


class PriceListItem(Base):
    """Price of a variant in a price list with validity timestamp (B8, B12)."""

    __tablename__ = "price_list_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    price_list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("price_lists.id"), nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id"), nullable=False, index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    price_list: Mapped[PriceList] = relationship(back_populates="items", lazy="joined")
    variant: Mapped[ProductVariant] = relationship(back_populates="price_items")

    def __repr__(self) -> str:
        return f"<PriceListItem variant={self.variant_id} price={self.price}>"
