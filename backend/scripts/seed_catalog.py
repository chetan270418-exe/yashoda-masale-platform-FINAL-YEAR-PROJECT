"""Seed product categories, spices, pack variants and price lists.

Usage:
    python -m scripts.seed_catalog

Idempotent — skips entities that already exist.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.core.db import get_engine

CATEGORIES = [
    {"name": "Blended Masalas", "description": "Authentic traditional ground spice blends"},
    {"name": "Pure Spices", "description": "Single origin pure spice powders"},
    {"name": "Whole Spices", "description": "Hand-picked whole aromatic spices"},
]

PRICE_LISTS = [
    {"name": "MRP", "description": "Maximum Retail Price for consumers"},
    {"name": "DEALER", "description": "Tiered price for authorized retail dealers"},
    {"name": "DISTRIBUTOR", "description": "Wholesale bulk price for regional distributors"},
]

PRODUCTS = [
    {
        "name": "Garam Masala",
        "category": "Blended Masalas",
        "description": "Signature aromatic 16-spice warm blend",
        "variants": [
            {
                "sku": "GM-100G",
                "pack_size_g": 100,
                "mrp": "85.00",
                "dealer": "68.00",
                "dist": "60.00",
            },
            {
                "sku": "GM-250G",
                "pack_size_g": 250,
                "mrp": "205.00",
                "dealer": "164.00",
                "dist": "145.00",
            },
            {
                "sku": "GM-500G",
                "pack_size_g": 500,
                "mrp": "395.00",
                "dealer": "316.00",
                "dist": "280.00",
            },
        ],
    },
    {
        "name": "Turmeric Powder (Haldi)",
        "category": "Pure Spices",
        "description": "High-curcumin sun-dried Salem turmeric ground to fine perfection",
        "variants": [
            {
                "sku": "TP-100G",
                "pack_size_g": 100,
                "mrp": "42.00",
                "dealer": "34.00",
                "dist": "30.00",
            },
            {
                "sku": "TP-250G",
                "pack_size_g": 250,
                "mrp": "100.00",
                "dealer": "80.00",
                "dist": "71.00",
            },
            {
                "sku": "TP-500G",
                "pack_size_g": 500,
                "mrp": "190.00",
                "dealer": "152.00",
                "dist": "135.00",
            },
        ],
    },
    {
        "name": "Red Chilli Powder (Mirchi)",
        "category": "Pure Spices",
        "description": "Stemless Guntur red chillies for vibrant colour and authentic heat",
        "variants": [
            {
                "sku": "RC-100G",
                "pack_size_g": 100,
                "mrp": "55.00",
                "dealer": "44.00",
                "dist": "39.00",
            },
            {
                "sku": "RC-250G",
                "pack_size_g": 250,
                "mrp": "130.00",
                "dealer": "104.00",
                "dist": "92.00",
            },
            {
                "sku": "RC-500G",
                "pack_size_g": 500,
                "mrp": "250.00",
                "dealer": "200.00",
                "dist": "178.00",
            },
        ],
    },
]


def seed_catalog(session: Session) -> None:
    print("Seeding price lists …")
    price_lists: dict[str, PriceList] = {}
    for pl_data in PRICE_LISTS:
        pl = session.scalars(select(PriceList).where(PriceList.name == pl_data["name"])).first()
        if not pl:
            pl = PriceList(name=pl_data["name"], description=pl_data["description"])
            session.add(pl)
            session.flush()
            print(f"  + PriceList: {pl.name}")
        else:
            print(f"  ✓ PriceList: {pl.name} (exists)")
        price_lists[pl.name] = pl

    print("Seeding categories …")
    categories: dict[str, Category] = {}
    for cat_data in CATEGORIES:
        cat = session.scalars(select(Category).where(Category.name == cat_data["name"])).first()
        if not cat:
            cat = Category(name=cat_data["name"], description=cat_data["description"])
            session.add(cat)
            session.flush()
            print(f"  + Category: {cat.name}")
        else:
            print(f"  ✓ Category: {cat.name} (exists)")
        categories[cat.name] = cat

    print("Seeding products & variants …")
    for prod_data in PRODUCTS:
        cat = categories[prod_data["category"]]
        prod = session.scalars(select(Product).where(Product.name == prod_data["name"])).first()
        if not prod:
            prod = Product(
                name=prod_data["name"],
                category_id=cat.id,
                description=prod_data["description"],
                is_active=True,
            )
            session.add(prod)
            session.flush()
            print(f"  + Product: {prod.name}")
        else:
            print(f"  ✓ Product: {prod.name} (exists)")

        for var_data in prod_data["variants"]:
            variant = session.scalars(
                select(ProductVariant).where(ProductVariant.sku == var_data["sku"])
            ).first()
            if not variant:
                variant = ProductVariant(
                    product_id=prod.id,
                    sku=var_data["sku"],
                    pack_size_g=var_data["pack_size_g"],
                    is_active=True,
                )
                session.add(variant)
                session.flush()
                print(f"    + Variant: {variant.sku} ({variant.pack_size_g}g)")

                # Seed prices in MRP, DEALER, DISTRIBUTOR
                session.add(
                    PriceListItem(
                        price_list_id=price_lists["MRP"].id,
                        variant_id=variant.id,
                        price=Decimal(var_data["mrp"]),
                    )
                )
                session.add(
                    PriceListItem(
                        price_list_id=price_lists["DEALER"].id,
                        variant_id=variant.id,
                        price=Decimal(var_data["dealer"]),
                    )
                )
                session.add(
                    PriceListItem(
                        price_list_id=price_lists["DISTRIBUTOR"].id,
                        variant_id=variant.id,
                        price=Decimal(var_data["dist"]),
                    )
                )
            else:
                print(f"    ✓ Variant: {variant.sku} (exists)")

    session.commit()
    print("Catalog seeding complete.")


if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker

    engine = get_engine()
    with sessionmaker(bind=engine)() as session:
        seed_catalog(session)
