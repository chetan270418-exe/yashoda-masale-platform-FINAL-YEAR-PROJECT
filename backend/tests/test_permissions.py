"""Permission Matrix Tests (T-025).

Systematically tests every role against every protected route:
- Anonymous (unauthenticated)
- Consumer
- Dealer
- Distributor
- Factory Manager
- Warehouse Manager
- Owner

Verifies that unauthorized access strictly returns HTTP 401 (missing auth) or
HTTP 403 (insufficient role permissions).
"""

import uuid
from decimal import Decimal

import pytest

from app.auth.security import create_access_token
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from tests.conftest import _make_user

NON_OWNER_ROLES = [
    "consumer",
    "dealer",
    "distributor",
    "factory_manager",
    "warehouse_manager",
]


@pytest.fixture()
def catalog_fixture(db):
    cat = Category(name="Matrix Spices")
    db.add(cat)
    db.flush()

    prod = Product(name="Matrix Masala", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(product_id=prod.id, sku="MM-100G", pack_size_g=100)
    db.add(var)
    db.flush()

    mrp_pl = db.query(PriceList).filter_by(name="MRP").first()
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var.id, price=Decimal("50.00")))
    db.commit()

    return {"category": cat, "product": prod, "variant": var}


# ── Public Routes (All Roles Allowed) ─────────────────────────────────────────


@pytest.mark.parametrize(
    "role",
    [
        "anonymous",
        "consumer",
        "dealer",
        "distributor",
        "factory_manager",
        "warehouse_manager",
        "owner",
    ],
)
def test_public_routes_accessible_to_all(client, db, role):
    headers = {}
    if role != "anonymous":
        user = _make_user(db, email=f"{role}_pub@test.com", role_name=role)
        token = create_access_token({"sub": str(user.id), "role": role})
        headers = {"Authorization": f"Bearer {token}"}

    r_health = client.get("/health", headers=headers)
    assert r_health.status_code == 200

    r_cats = client.get("/api/v1/categories", headers=headers)
    assert r_cats.status_code == 200

    r_prods = client.get("/api/v1/products", headers=headers)
    assert r_prods.status_code == 200


# ── Authenticated Route: /api/v1/auth/me ──────────────────────────────────────


def test_me_denied_for_anonymous(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.parametrize(
    "role", ["consumer", "dealer", "distributor", "factory_manager", "warehouse_manager", "owner"]
)
def test_me_allowed_for_any_authenticated_role(client, db, role):
    user = _make_user(db, email=f"{role}_me@test.com", role_name=role)
    token = create_access_token({"sub": str(user.id), "role": role})

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == f"{role}_me@test.com"


# ── Owner-Only Routes: Non-Owners Denied (HTTP 403) ───────────────────────────


@pytest.mark.parametrize("role", NON_OWNER_ROLES)
def test_list_users_denied_for_non_owners(client, db, role):
    user = _make_user(db, email=f"{role}_u@test.com", role_name=role)
    token = create_access_token({"sub": str(user.id), "role": role})

    r = client.get("/api/v1/auth/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", NON_OWNER_ROLES)
def test_create_staff_denied_for_non_owners(client, db, role):
    user = _make_user(db, email=f"{role}_staff@test.com", role_name=role)
    token = create_access_token({"sub": str(user.id), "role": role})

    r = client.post(
        "/api/v1/auth/staff",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": f"new_{role}@test.com",
            "password": "password123",
            "full_name": "Test Staff",
            "role_name": "warehouse_manager",
        },
    )
    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", NON_OWNER_ROLES)
def test_create_category_denied_for_non_owners(client, db, role):
    user = _make_user(db, email=f"{role}_cat@test.com", role_name=role)
    token = create_access_token({"sub": str(user.id), "role": role})

    r = client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"Cat from {role}"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", NON_OWNER_ROLES)
def test_create_product_denied_for_non_owners(client, db, catalog_fixture, role):
    user = _make_user(db, email=f"{role}_prod@test.com", role_name=role)
    token = create_access_token({"sub": str(user.id), "role": role})

    r = client.post(
        "/api/v1/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": f"Illegal Prod from {role}",
            "category_id": str(catalog_fixture["category"].id),
            "variants": [{"sku": f"SKU-{role.upper()}", "pack_size_g": 100}],
        },
    )
    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", NON_OWNER_ROLES)
def test_set_price_denied_for_non_owners(client, db, catalog_fixture, role):
    user = _make_user(db, email=f"{role}_price@test.com", role_name=role)
    token = create_access_token({"sub": str(user.id), "role": role})

    variant_id = catalog_fixture["variant"].id
    r = client.put(
        f"/api/v1/products/variants/{variant_id}/prices/MRP",
        headers={"Authorization": f"Bearer {token}"},
        json={"price": "99.99"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"


# ── Owner-Only Routes: Owner Allowed (HTTP 200 / 201) ─────────────────────────


def test_owner_access_allowed_on_all_admin_routes(client, db, catalog_fixture):
    owner = _make_user(db, email="super_owner@test.com", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Users list
    r_users = client.get("/api/v1/auth/users", headers=headers)
    assert r_users.status_code == 200

    # 2. Staff create
    r_staff = client.post(
        "/api/v1/auth/staff",
        headers=headers,
        json={
            "email": f"allowed_staff_{uuid.uuid4().hex[:6]}@test.com",
            "password": "securepassword",
            "full_name": "Allowed Staff",
            "role_name": "factory_manager",
        },
    )
    assert r_staff.status_code == 201

    # 3. Category create
    r_cat = client.post(
        "/api/v1/categories",
        headers=headers,
        json={"name": f"Owner Cat {uuid.uuid4().hex[:6]}"},
    )
    assert r_cat.status_code == 201

    # 4. Product create
    r_prod = client.post(
        "/api/v1/products",
        headers=headers,
        json={
            "name": f"Owner Spice {uuid.uuid4().hex[:6]}",
            "category_id": str(catalog_fixture["category"].id),
            "variants": [{"sku": f"OS-{uuid.uuid4().hex[:6]}", "pack_size_g": 200}],
        },
    )
    assert r_prod.status_code == 201

    # 5. Price update
    r_price = client.put(
        f"/api/v1/products/variants/{catalog_fixture['variant'].id}/prices/MRP",
        headers=headers,
        json={"price": "55.00"},
    )
    assert r_price.status_code == 200
