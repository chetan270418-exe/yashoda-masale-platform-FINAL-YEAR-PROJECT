"""Catalog module tests (T-023).

Covers:
- Category CRUD and permissions (owner only for create)
- Product & Variant creation with duplicate SKU prevention
- Role-based price resolution (B9, FR-CAT-03)
  - Consumer / Anonymous sees MRP
  - Dealer sees DEALER price
  - Distributor sees DISTRIBUTOR price
  - Dealer cannot see distributor price
- Price update writes to audit log (FR-AUTH-05)
- Money decimal precision (B12)
"""

from decimal import Decimal

from app.audit.models import AuditLog
from app.auth.security import create_access_token
from app.catalog import service
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from tests.conftest import _make_user


def _setup_spice_catalog(db):
    """Helper fixture: create category, product, variants and 3 price tiers."""
    cat = Category(name="Blended Masalas", description="Aromatic blends")
    db.add(cat)
    db.flush()

    prod = Product(name="Garam Masala", category_id=cat.id, description="Signature warm blend")
    db.add(prod)
    db.flush()

    v100 = ProductVariant(product_id=prod.id, sku="GM-100G", pack_size_g=100)
    v500 = ProductVariant(product_id=prod.id, sku="GM-500G", pack_size_g=500)
    db.add_all([v100, v500])
    db.flush()

    mrp_pl = db.query(PriceList).filter_by(name="MRP").first()
    dealer_pl = db.query(PriceList).filter_by(name="DEALER").first()
    dist_pl = db.query(PriceList).filter_by(name="DISTRIBUTOR").first()

    # Prices for 100g: MRP=85.00, Dealer=68.00, Dist=60.00
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=v100.id, price=Decimal("85.00")))
    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=v100.id, price=Decimal("68.00")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=v100.id, price=Decimal("60.00")))

    # Prices for 500g: MRP=395.00, Dealer=316.00, Dist=280.00
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=v500.id, price=Decimal("395.00")))
    db.add(PriceListItem(price_list_id=dealer_pl.id, variant_id=v500.id, price=Decimal("316.00")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=v500.id, price=Decimal("280.00")))

    db.commit()
    return prod, v100, v500


# ── Category Tests ────────────────────────────────────────────────────────────


def test_public_can_list_categories(client, db):
    db.add(Category(name="Whole Spices"))
    db.commit()

    r = client.get("/api/v1/categories")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert "Whole Spices" in names


def test_owner_can_create_category(client, db):
    owner = _make_user(db, email="cat_owner@test.com", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    r = client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Pure Spices", "description": "Single-ingredient powders"},
    )
    assert r.status_code == 201
    assert r.json()["name"] == "Pure Spices"


def test_consumer_cannot_create_category(client, db):
    consumer = _make_user(db, email="cat_consumer@test.com", role_name="consumer")
    token = create_access_token({"sub": str(consumer.id), "role": "consumer"})

    r = client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Illegal Cat"},
    )
    assert r.status_code == 403


# ── Product & Variant Creation ────────────────────────────────────────────────


def test_owner_can_create_product_with_variants(client, db):
    owner = _make_user(db, email="prod_owner@test.com", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    cat = Category(name="Blended")
    db.add(cat)
    db.commit()

    r = client.post(
        "/api/v1/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Chana Masala",
            "category_id": str(cat.id),
            "description": "Tangy chickpea blend",
            "variants": [
                {"sku": "CM-100G", "pack_size_g": 100},
                {"sku": "CM-250G", "pack_size_g": 250},
            ],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Chana Masala"
    assert len(data["variants"]) == 2
    assert {v["sku"] for v in data["variants"]} == {"CM-100G", "CM-250G"}


def test_duplicate_sku_rejected(client, db):
    owner = _make_user(db, email="dup_owner@test.com", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    cat = Category(name="Blended 2")
    db.add(cat)
    db.commit()

    # Create first product with SKU
    client.post(
        "/api/v1/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Blend A",
            "category_id": str(cat.id),
            "variants": [{"sku": "UNIQUE-SKU", "pack_size_g": 100}],
        },
    )

    # Attempt to create duplicate SKU on another product
    r = client.post(
        "/api/v1/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Blend B",
            "category_id": str(cat.id),
            "variants": [{"sku": "UNIQUE-SKU", "pack_size_g": 250}],
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "DUPLICATE_SKU"


# ── Role-based Price Resolution (B9, FR-CAT-03) ───────────────────────────────


def test_anonymous_browsing_sees_mrp(client, db):
    prod, v100, v500 = _setup_spice_catalog(db)

    r = client.get("/api/v1/products")
    assert r.status_code == 200
    products = r.json()
    assert len(products) >= 1

    gm = next(p for p in products if p["id"] == str(prod.id))
    prices_by_sku = {v["sku"]: Decimal(str(v["price"])) for v in gm["variants"]}

    # Anonymous user must see MRP
    assert prices_by_sku["GM-100G"] == Decimal("85.00")
    assert prices_by_sku["GM-500G"] == Decimal("395.00")


def test_consumer_sees_mrp(client, db):
    prod, v100, _ = _setup_spice_catalog(db)
    consumer = _make_user(db, email="consumer_browse@test.com", role_name="consumer")
    token = create_access_token({"sub": str(consumer.id), "role": "consumer"})

    r = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    gm = next(p for p in r.json() if p["id"] == str(prod.id))
    v100_data = next(v for v in gm["variants"] if v["sku"] == "GM-100G")

    assert Decimal(str(v100_data["price"])) == Decimal("85.00")


def test_dealer_sees_dealer_price_and_not_distributor(client, db):
    """B9: Server resolves DEALER price. Dealer must NEVER see distributor price."""
    prod, v100, v500 = _setup_spice_catalog(db)
    dealer = _make_user(db, email="dealer_browse@test.com", role_name="dealer")
    token = create_access_token({"sub": str(dealer.id), "role": "dealer"})

    r = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    gm = next(p for p in r.json() if p["id"] == str(prod.id))
    prices_by_sku = {v["sku"]: Decimal(str(v["price"])) for v in gm["variants"]}

    # Dealer must see 68.00 and 316.00 (NOT distributor 60.00 / 280.00, NOT MRP 85.00)
    assert prices_by_sku["GM-100G"] == Decimal("68.00")
    assert prices_by_sku["GM-500G"] == Decimal("316.00")


def test_distributor_sees_distributor_price(client, db):
    prod, v100, v500 = _setup_spice_catalog(db)
    distributor = _make_user(db, email="dist_browse@test.com", role_name="distributor")
    token = create_access_token({"sub": str(distributor.id), "role": "distributor"})

    r = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    gm = next(p for p in r.json() if p["id"] == str(prod.id))
    prices_by_sku = {v["sku"]: Decimal(str(v["price"])) for v in gm["variants"]}

    # Distributor must see 60.00 and 280.00
    assert prices_by_sku["GM-100G"] == Decimal("60.00")
    assert prices_by_sku["GM-500G"] == Decimal("280.00")


def test_single_product_lookup_resolves_dealer_price(client, db):
    prod, v100, _ = _setup_spice_catalog(db)
    dealer = _make_user(db, email="dealer_single@test.com", role_name="dealer")
    token = create_access_token({"sub": str(dealer.id), "role": "dealer"})

    r = client.get(f"/api/v1/products/{prod.id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    gm = r.json()
    v100_data = next(v for v in gm["variants"] if v["sku"] == "GM-100G")
    assert Decimal(str(v100_data["price"])) == Decimal("68.00")


def test_deactivated_dealer_falls_back_to_mrp(client, db):
    """Item 5: Deactivated dealers are treated as anonymous and see MRP, not DEALER price."""
    prod, v100, _ = _setup_spice_catalog(db)
    dealer = _make_user(db, email="deactivated_dealer@test.com", role_name="dealer")
    token = create_access_token({"sub": str(dealer.id), "role": "dealer"})

    # Deactivate dealer
    dealer.is_active = False
    db.commit()

    r = client.get("/api/v1/products", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    gm = next(p for p in r.json() if p["id"] == str(prod.id))
    v100_data = next(v for v in gm["variants"] if v["sku"] == "GM-100G")

    # Must resolve to MRP 85.00, NOT Dealer 68.00
    assert Decimal(str(v100_data["price"])) == Decimal("85.00")


# ── Price Update and Audit Log ────────────────────────────────────────────────


def test_price_update_creates_audit_log(client, db):
    """FR-AUTH-05: Price changes must be logged in audit_logs."""
    _, v100, _ = _setup_spice_catalog(db)
    owner = _make_user(db, email="price_changer@test.com", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    # Update MRP price from 85.00 to 89.00
    r = client.put(
        f"/api/v1/products/variants/{v100.id}/prices/MRP",
        headers={"Authorization": f"Bearer {token}"},
        json={"price": "89.00"},
    )
    assert r.status_code == 200

    # Verify audit log recorded the action
    audit = (
        db.query(AuditLog)
        .filter_by(action="UPDATE_PRICE")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert audit is not None
    assert audit.actor_id == owner.id
    assert audit.before["price"] == "85.00"
    assert audit.after["price"] == "89.00"

    # Verify the new price resolves on subsequent query
    updated_price = service.get_product_for_user(db, v100.product_id, role_name="consumer")
    v100_read = next(v for v in updated_price.variants if v.sku == "GM-100G")
    assert v100_read.price == Decimal("89.00")
