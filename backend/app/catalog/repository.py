"""Catalog database repository layer."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant

# ── Categories ────────────────────────────────────────────────────────────────


def get_category_by_id(db: Session, category_id: uuid.UUID) -> Category | None:
    return db.get(Category, category_id)


def get_category_by_name(db: Session, name: str) -> Category | None:
    stmt = select(Category).where(Category.name == name)
    return db.scalars(stmt).first()


def list_categories(db: Session) -> list[Category]:
    return list(db.scalars(select(Category).order_by(Category.name)).all())


def create_category(db: Session, name: str, description: str | None = None) -> Category:
    category = Category(name=name, description=description)
    db.add(category)
    db.flush()
    return category


# ── Products ──────────────────────────────────────────────────────────────────


def get_product_by_id(db: Session, product_id: uuid.UUID) -> Product | None:
    return db.get(Product, product_id)


def list_products(
    db: Session, category_id: uuid.UUID | None = None, active_only: bool = True
) -> list[Product]:
    stmt = select(Product)
    if active_only:
        stmt = stmt.where(Product.is_active.is_(True))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    stmt = stmt.order_by(Product.name)
    return list(db.scalars(stmt).all())


def create_product(
    db: Session,
    name: str,
    category_id: uuid.UUID,
    description: str | None = None,
    is_active: bool = True,
) -> Product:
    product = Product(
        name=name, category_id=category_id, description=description, is_active=is_active
    )
    db.add(product)
    db.flush()
    return product


def update_product(db: Session, product: Product, **kwargs) -> Product:  # noqa: ANN003
    for key, value in kwargs.items():
        if value is not None and hasattr(product, key):
            setattr(product, key, value)
    db.flush()
    return product


# ── Variants ──────────────────────────────────────────────────────────────────


def get_variant_by_id(db: Session, variant_id: uuid.UUID) -> ProductVariant | None:
    return db.get(ProductVariant, variant_id)


def get_variant_by_sku(db: Session, sku: str) -> ProductVariant | None:
    stmt = select(ProductVariant).where(ProductVariant.sku == sku)
    return db.scalars(stmt).first()


def create_variant(
    db: Session,
    product_id: uuid.UUID,
    sku: str,
    pack_size_g: int,
    image_url: str | None = None,
    is_active: bool = True,
) -> ProductVariant:
    variant = ProductVariant(
        product_id=product_id,
        sku=sku,
        pack_size_g=pack_size_g,
        image_url=image_url,
        is_active=is_active,
    )
    db.add(variant)
    db.flush()
    return variant


# ── Price Lists & Items ───────────────────────────────────────────────────────


def get_price_list_by_name(db: Session, name: str) -> PriceList | None:
    stmt = select(PriceList).where(PriceList.name == name)
    return db.scalars(stmt).first()


def list_price_lists(db: Session) -> list[PriceList]:
    return list(db.scalars(select(PriceList).order_by(PriceList.name)).all())


def create_price_list(db: Session, name: str, description: str | None = None) -> PriceList:
    price_list = PriceList(name=name, description=description)
    db.add(price_list)
    db.flush()
    return price_list


def get_latest_price_for_variant(
    db: Session,
    price_list_id: uuid.UUID,
    variant_id: uuid.UUID,
    at: datetime | None = None,
) -> Decimal | None:
    """Resolve the active unit price as of *at* timestamp (implementation.md section 4)."""
    now = at or datetime.now(UTC)
    stmt = (
        select(PriceListItem.price)
        .where(
            PriceListItem.price_list_id == price_list_id,
            PriceListItem.variant_id == variant_id,
            PriceListItem.valid_from <= now,
        )
        .order_by(PriceListItem.valid_from.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def get_prices_for_variants(
    db: Session,
    price_list_id: uuid.UUID,
    variant_ids: list[uuid.UUID],
    at: datetime | None = None,
) -> dict[uuid.UUID, Decimal]:
    """Resolve active unit prices for multiple variants in one pass."""
    if not variant_ids:
        return {}

    now = at or datetime.now(UTC)
    stmt = (
        select(PriceListItem.variant_id, PriceListItem.price, PriceListItem.valid_from)
        .where(
            PriceListItem.price_list_id == price_list_id,
            PriceListItem.variant_id.in_(variant_ids),
            PriceListItem.valid_from <= now,
        )
        .order_by(PriceListItem.variant_id, PriceListItem.valid_from.desc())
    )
    rows = db.execute(stmt).all()

    # Take the latest price per variant
    resolved: dict[uuid.UUID, Decimal] = {}
    for var_id, price, _ in rows:
        if var_id not in resolved:
            resolved[var_id] = price
    return resolved


def add_price_list_item(
    db: Session,
    price_list_id: uuid.UUID,
    variant_id: uuid.UUID,
    price: Decimal,
    valid_from: datetime | None = None,
) -> PriceListItem:
    item = PriceListItem(
        price_list_id=price_list_id,
        variant_id=variant_id,
        price=price,
        valid_from=valid_from or datetime.now(UTC),
    )
    db.add(item)
    db.flush()
    return item
