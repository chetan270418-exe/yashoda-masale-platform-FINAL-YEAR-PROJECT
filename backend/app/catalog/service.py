"""Catalog service: product management and role-based price resolution.

Rules: FR-CAT-01, FR-CAT-03, B8, B9, B12.
"""

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.audit.service import log_action
from app.catalog import repository as repo
from app.catalog.models import Product
from app.catalog.schemas import (
    CategoryCreate,
    CategoryRead,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    VariantCreate,
    VariantRead,
)
from app.core.errors import AppError

# Canonical price list names
PRICE_LIST_MRP = "MRP"
PRICE_LIST_DEALER = "DEALER"
PRICE_LIST_DISTRIBUTOR = "DISTRIBUTOR"


def resolve_price_list_name_for_role(role_name: str | None) -> str:
    """Determine which price list a given user role may see (B9, FR-CAT-03).

    - dealer -> DEALER
    - distributor -> DISTRIBUTOR
    - consumer or unauthenticated -> MRP
    """
    if role_name == "dealer":
        return PRICE_LIST_DEALER
    if role_name == "distributor":
        return PRICE_LIST_DISTRIBUTOR
    return PRICE_LIST_MRP


def create_category(db: Session, data: CategoryCreate) -> CategoryRead:
    if repo.get_category_by_name(db, data.name):
        raise AppError("DUPLICATE_CATEGORY", f"Category '{data.name}' already exists.", 409)
    category = repo.create_category(db, data.name, data.description)
    db.commit()
    return CategoryRead.model_validate(category)


def list_categories(db: Session) -> list[CategoryRead]:
    categories = repo.list_categories(db)
    return [CategoryRead.model_validate(c) for c in categories]


def create_product(db: Session, data: ProductCreate, actor_id: uuid.UUID) -> ProductRead:
    """Create a product and optional initial variants."""
    category = repo.get_category_by_id(db, data.category_id)
    if not category:
        raise AppError("CATEGORY_NOT_FOUND", "Specified category does not exist.", 404)

    product = repo.create_product(
        db,
        name=data.name,
        category_id=data.category_id,
        description=data.description,
        is_active=data.is_active,
    )

    for v in data.variants:
        if repo.get_variant_by_sku(db, v.sku):
            raise AppError("DUPLICATE_SKU", f"SKU '{v.sku}' already exists.", 409)
        repo.create_variant(
            db,
            product_id=product.id,
            sku=v.sku,
            pack_size_g=v.pack_size_g,
            image_url=v.image_url,
            is_active=v.is_active,
        )

    log_action(
        db,
        actor_id=actor_id,
        action="CREATE_PRODUCT",
        entity="products",
        entity_id=product.id,
        after={"name": product.name, "category_id": str(category.id)},
    )
    db.commit()
    db.refresh(product)
    return _build_product_read(db, product, price_list_name=PRICE_LIST_MRP)


def add_variant_to_product(
    db: Session, product_id: uuid.UUID, data: VariantCreate, actor_id: uuid.UUID
) -> VariantRead:
    product = repo.get_product_by_id(db, product_id)
    if not product:
        raise AppError("PRODUCT_NOT_FOUND", "Product does not exist.", 404)

    if repo.get_variant_by_sku(db, data.sku):
        raise AppError("DUPLICATE_SKU", f"SKU '{data.sku}' already exists.", 409)

    variant = repo.create_variant(
        db,
        product_id=product.id,
        sku=data.sku,
        pack_size_g=data.pack_size_g,
        image_url=data.image_url,
        is_active=data.is_active,
    )
    log_action(
        db,
        actor_id=actor_id,
        action="CREATE_VARIANT",
        entity="product_variants",
        entity_id=variant.id,
        after={"sku": variant.sku, "product_id": str(product.id)},
    )
    db.commit()
    return VariantRead(
        id=variant.id,
        product_id=variant.product_id,
        sku=variant.sku,
        pack_size_g=variant.pack_size_g,
        image_url=variant.image_url,
        is_active=variant.is_active,
        price=None,
    )


def update_product(
    db: Session, product_id: uuid.UUID, data: ProductUpdate, actor_id: uuid.UUID
) -> ProductRead:
    product = repo.get_product_by_id(db, product_id)
    if not product:
        raise AppError("PRODUCT_NOT_FOUND", "Product does not exist.", 404)

    before = {
        "name": product.name,
        "category_id": str(product.category_id),
        "is_active": product.is_active,
    }
    product = repo.update_product(db, product, **data.model_dump(exclude_unset=True))
    after = {
        "name": product.name,
        "category_id": str(product.category_id),
        "is_active": product.is_active,
    }

    log_action(
        db,
        actor_id=actor_id,
        action="UPDATE_PRODUCT",
        entity="products",
        entity_id=product.id,
        before=before,
        after=after,
    )
    db.commit()
    db.refresh(product)
    return _build_product_read(db, product, price_list_name=PRICE_LIST_MRP)


def set_variant_price(
    db: Session,
    variant_id: uuid.UUID,
    price_list_name: str,
    price: Decimal,
    actor_id: uuid.UUID,
) -> None:
    """Set or update variant price in a price list, preserving history (B8, B12, FR-AUTH-05)."""
    variant = repo.get_variant_by_id(db, variant_id)
    if not variant:
        raise AppError("VARIANT_NOT_FOUND", "Variant does not exist.", 404)

    price_list = repo.get_price_list_by_name(db, price_list_name)
    if not price_list:
        raise AppError(
            "PRICE_LIST_NOT_FOUND", f"Price list '{price_list_name}' does not exist.", 404
        )

    old_price = repo.get_latest_price_for_variant(db, price_list.id, variant.id)
    item = repo.add_price_list_item(db, price_list.id, variant.id, price)

    log_action(
        db,
        actor_id=actor_id,
        action="UPDATE_PRICE",
        entity="price_list_items",
        entity_id=item.id,
        before={"price_list": price_list_name, "price": str(old_price) if old_price else None},
        after={"price_list": price_list_name, "price": str(price)},
    )
    db.commit()


def get_products_for_user(
    db: Session,
    role_name: str | None,
    category_id: uuid.UUID | None = None,
    active_only: bool = True,
) -> list[ProductRead]:
    """List products with prices resolved specifically for the user's role (B9)."""
    price_list_name = resolve_price_list_name_for_role(role_name)
    products = repo.list_products(db, category_id=category_id, active_only=active_only)

    price_list = repo.get_price_list_by_name(db, price_list_name)
    price_list_id = price_list.id if price_list else None

    # Collect all variant IDs to resolve prices in a single query
    all_variant_ids = [
        v.id for p in products for v in p.variants if (not active_only or v.is_active)
    ]
    prices: dict[uuid.UUID, Decimal] = {}
    if price_list_id and all_variant_ids:
        prices = repo.get_prices_for_variants(db, price_list_id, all_variant_ids)

    results: list[ProductRead] = []
    for product in products:
        var_reads = [
            VariantRead(
                id=v.id,
                product_id=v.product_id,
                sku=v.sku,
                pack_size_g=v.pack_size_g,
                image_url=v.image_url,
                is_active=v.is_active,
                price=prices.get(v.id),
            )
            for v in product.variants
            if (not active_only or v.is_active)
        ]
        results.append(
            ProductRead(
                id=product.id,
                name=product.name,
                category_id=product.category_id,
                category_name=product.category.name if product.category else None,
                description=product.description,
                is_active=product.is_active,
                created_at=product.created_at,
                variants=var_reads,
            )
        )
    return results


def get_product_for_user(db: Session, product_id: uuid.UUID, role_name: str | None) -> ProductRead:
    """Get single product with prices resolved for user's role."""
    product = repo.get_product_by_id(db, product_id)
    if not product:
        raise AppError("PRODUCT_NOT_FOUND", "Product does not exist.", 404)
    price_list_name = resolve_price_list_name_for_role(role_name)
    return _build_product_read(db, product, price_list_name)


def _build_product_read(db: Session, product: Product, price_list_name: str) -> ProductRead:
    price_list = repo.get_price_list_by_name(db, price_list_name)
    variant_ids = [v.id for v in product.variants]
    prices: dict[uuid.UUID, Decimal] = {}
    if price_list and variant_ids:
        prices = repo.get_prices_for_variants(db, price_list.id, variant_ids)

    variants_read = [
        VariantRead(
            id=v.id,
            product_id=v.product_id,
            sku=v.sku,
            pack_size_g=v.pack_size_g,
            image_url=v.image_url,
            is_active=v.is_active,
            price=prices.get(v.id),
        )
        for v in product.variants
    ]

    return ProductRead(
        id=product.id,
        name=product.name,
        category_id=product.category_id,
        category_name=product.category.name if product.category else None,
        description=product.description,
        is_active=product.is_active,
        created_at=product.created_at,
        variants=variants_read,
    )
