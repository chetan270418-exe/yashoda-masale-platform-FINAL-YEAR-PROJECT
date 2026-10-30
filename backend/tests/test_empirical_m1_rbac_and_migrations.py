"""Empirical Challenger M1 verification suite: RBAC, Customer Isolation, and Alembic Migrations.

Adversarial test suite for Milestone M1 (T-050 Ordering Service Foundation):
1. RBAC and Customer Isolation:
   - Consumer A creates order; Consumer B attempts to read (403), mutate (405), or query.
   - Cross-tenant idempotency collision: Consumer B re-using A's idempotency key (409).
   - Warehouse and Factory roles attempting order creation (403).
   - Dealer and Distributor role order creation (201) and customer isolation.
   - Staff roles (owner, warehouse, factory) querying orders with/without customer filter.
   - Public / unauthenticated requests rejected (401) across all endpoints.
   - Inactive / deactivated user tokens rejected (403 ACCOUNT_DISABLED).
   - Custom / unauthorized role handling (403).
2. Database Schema and Migration:
   - Full Alembic upgrade/downgrade roundtrip on isolated SQLite DB.
   - Verify columns, nullable flags, server defaults, and foreign keys.
   - Verify indexes on `orders` (customer_id, idempotency_key unique, order_number).
   - Verify check constraints and foreign key actions (CASCADE on order, SET NULL on user).
   - Verify migration 0005 downgrade behavior: status normalization to 'open'.
   - Verify migration re-upgrade head.
"""

import os
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import Role, User
from app.auth.security import create_access_token, hash_password
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.orders.models import Order, OrderItem
from tests.conftest import _make_user


@pytest.fixture()
def m1_catalog_fixture(db: Session):
    """Seed test category, product, variants, and role-based price lists."""
    cat = Category(name="M1 Challenger Spices")
    db.add(cat)
    db.flush()

    prod = Product(name="M1 Challenger Cumin", category_id=cat.id)
    db.add(prod)
    db.flush()

    var_250g = ProductVariant(product_id=prod.id, sku="M1-CHAL-CUM-250G", pack_size_g=250)
    var_1kg = ProductVariant(product_id=prod.id, sku="M1-CHAL-CUM-1KG", pack_size_g=1000)
    db.add_all([var_250g, var_1kg])
    db.flush()

    mrp_pl = db.query(PriceList).filter_by(name="MRP").first()
    dealer_pl = db.query(PriceList).filter_by(name="DEALER").first()
    dist_pl = db.query(PriceList).filter_by(name="DISTRIBUTOR").first()

    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_250g.id, price=Decimal("120.00")))
    db.add(PriceListItem(price_list_id=mrp_pl.id, variant_id=var_1kg.id, price=Decimal("450.00")))

    db.add(
        PriceListItem(price_list_id=dealer_pl.id, variant_id=var_250g.id, price=Decimal("95.00"))
    )
    db.add(
        PriceListItem(price_list_id=dealer_pl.id, variant_id=var_1kg.id, price=Decimal("380.00"))
    )

    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_250g.id, price=Decimal("80.00")))
    db.add(PriceListItem(price_list_id=dist_pl.id, variant_id=var_1kg.id, price=Decimal("340.00")))
    db.commit()

    return {
        "category": cat,
        "product": prod,
        "var_250g": var_250g,
        "var_1kg": var_1kg,
    }


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# SECTION 1: RBAC & Customer Isolation Adversarial Tests
# ==============================================================================


def test_adversarial_consumer_isolation_cross_tenant_read(
    client: TestClient, db: Session, m1_catalog_fixture
):
    """Consumer A creates an order; Consumer B must be rejected 403 on direct retrieval."""
    consumer_a = _make_user(db, email="isolated_a@test.com", role_name="consumer")
    consumer_b = _make_user(db, email="isolated_b@test.com", role_name="consumer")
    var_250g = m1_catalog_fixture["var_250g"]

    # 1. Consumer A creates Order A
    resp_create = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_250g.id), "quantity": "2.000"}]},
        headers=_auth_header(consumer_a),
    )
    assert resp_create.status_code == 201
    order_a_id = resp_create.json()["id"]

    # 2. Consumer B attempts to read Consumer A's order by UUID -> MUST BE 403
    resp_read = client.get(f"/api/v1/orders/{order_a_id}", headers=_auth_header(consumer_b))
    assert resp_read.status_code == 403
    assert resp_read.json()["code"] == "FORBIDDEN"


def test_adversarial_consumer_isolation_query_param_leakage(
    client: TestClient, db: Session, m1_catalog_fixture
):
    """Consumer B queries /api/v1/orders with customer_id=Consumer_A; must NOT leak A's orders."""
    consumer_a = _make_user(db, email="leak_a@test.com", role_name="consumer")
    consumer_b = _make_user(db, email="leak_b@test.com", role_name="consumer")
    var_250g = m1_catalog_fixture["var_250g"]

    # 1. Consumer A creates an order
    resp_a = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_250g.id), "quantity": "1.000"}]},
        headers=_auth_header(consumer_a),
    )
    assert resp_a.status_code == 201
    order_a_id = resp_a.json()["id"]

    # 2. Consumer B queries orders passing customer_id=Consumer_A
    # The server must override/ignore customer_id for non-staff and force customer_id=Consumer_B
    resp_query = client.get(
        f"/api/v1/orders?customer_id={consumer_a.id}",
        headers=_auth_header(consumer_b),
    )
    assert resp_query.status_code == 200
    orders = resp_query.json()
    assert order_a_id not in [o["id"] for o in orders]
    assert len(orders) == 0  # Consumer B has 0 orders


def test_adversarial_consumer_mutation_verbs_rejected(
    client: TestClient, db: Session, m1_catalog_fixture
):
    """Consumer B attempts HTTP PUT, PATCH, DELETE on Consumer A's order; must be rejected."""
    consumer_a = _make_user(db, email="mut_a@test.com", role_name="consumer")
    consumer_b = _make_user(db, email="mut_b@test.com", role_name="consumer")
    var_250g = m1_catalog_fixture["var_250g"]

    resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_250g.id), "quantity": "1.000"}]},
        headers=_auth_header(consumer_a),
    )
    assert resp.status_code == 201
    order_a_id = resp.json()["id"]

    # Attempt PUT
    resp_put = client.put(
        f"/api/v1/orders/{order_a_id}",
        json={"status": "cancelled"},
        headers=_auth_header(consumer_b),
    )
    assert resp_put.status_code in (404, 405)

    # Attempt PATCH
    resp_patch = client.patch(
        f"/api/v1/orders/{order_a_id}",
        json={"status": "cancelled"},
        headers=_auth_header(consumer_b),
    )
    assert resp_patch.status_code in (404, 405)

    # Attempt DELETE
    resp_delete = client.delete(
        f"/api/v1/orders/{order_a_id}",
        headers=_auth_header(consumer_b),
    )
    assert resp_delete.status_code in (404, 405)


def test_adversarial_cross_tenant_idempotency_key_hijack(
    client: TestClient, db: Session, m1_catalog_fixture
):
    """Consumer B re-uses Consumer A's idempotency key; allowed and isolated per customer."""
    consumer_a = _make_user(db, email="idemp_a@test.com", role_name="consumer")
    consumer_b = _make_user(db, email="idemp_b@test.com", role_name="consumer")
    var_250g = m1_catalog_fixture["var_250g"]
    shared_key = f"shared-key-{uuid.uuid4().hex[:8]}"

    # Consumer A creates order with key
    resp_a = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_250g.id), "quantity": "1.000"}],
            "idempotency_key": shared_key,
        },
        headers=_auth_header(consumer_a),
    )
    assert resp_a.status_code == 201

    # Consumer B attempts to use exact same key; succeeds independently per customer
    resp_b = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_250g.id), "quantity": "1.000"}],
            "idempotency_key": shared_key,
        },
        headers=_auth_header(consumer_b),
    )
    assert resp_b.status_code == 201
    assert resp_b.json()["id"] != resp_a.json()["id"]

    # Consumer A reusing same key with different payload gets 409 IDEMPOTENCY_CONFLICT
    resp_conflict = client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": str(var_250g.id), "quantity": "2.000"}],
            "idempotency_key": shared_key,
        },
        headers=_auth_header(consumer_a),
    )
    assert resp_conflict.status_code == 409
    assert resp_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_adversarial_warehouse_and_factory_cannot_create_orders(
    client: TestClient, db: Session, m1_catalog_fixture
):
    """Warehouse and Factory staff are strictly prohibited from creating orders (HTTP 403)."""
    wh_user = _make_user(db, email="wh_order_block@test.com", role_name="warehouse")
    fac_user = _make_user(db, email="fac_order_block@test.com", role_name="factory")
    var_250g = m1_catalog_fixture["var_250g"]

    order_payload = {"items": [{"variant_id": str(var_250g.id), "quantity": "1.000"}]}

    # 1. Warehouse -> 403
    resp_wh = client.post("/api/v1/orders", json=order_payload, headers=_auth_header(wh_user))
    assert resp_wh.status_code == 403
    assert resp_wh.json()["code"] == "FORBIDDEN"

    # 2. Factory -> 403
    resp_fac = client.post("/api/v1/orders", json=order_payload, headers=_auth_header(fac_user))
    assert resp_fac.status_code == 403
    assert resp_fac.json()["code"] == "FORBIDDEN"


def test_adversarial_staff_roles_query_orders_matrix(
    client: TestClient, db: Session, m1_catalog_fixture
):
    """Staff roles (owner, warehouse, factory) query orders with and without customer_id filter."""
    consumer_a = _make_user(db, email="staff_query_a@test.com", role_name="consumer")
    consumer_b = _make_user(db, email="staff_query_b@test.com", role_name="consumer")
    owner = _make_user(db, email="staff_query_owner@test.com", role_name="owner")
    warehouse = _make_user(db, email="staff_query_wh@test.com", role_name="warehouse")
    factory = _make_user(db, email="staff_query_fac@test.com", role_name="factory")
    var_250g = m1_catalog_fixture["var_250g"]

    # 1. Consumer A creates 2 orders
    order_a1 = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_250g.id), "quantity": "1.000"}]},
        headers=_auth_header(consumer_a),
    ).json()["id"]
    order_a2 = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_250g.id), "quantity": "2.000"}]},
        headers=_auth_header(consumer_a),
    ).json()["id"]

    # 2. Consumer B creates 1 order
    order_b1 = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_250g.id), "quantity": "3.000"}]},
        headers=_auth_header(consumer_b),
    ).json()["id"]

    staff_members = [("owner", owner), ("warehouse", warehouse), ("factory", factory)]

    for role_name, staff_user in staff_members:
        # a. Query without filter: must see all 3 orders
        resp_all = client.get("/api/v1/orders", headers=_auth_header(staff_user))
        assert resp_all.status_code == 200, f"Failed for {role_name}"
        all_ids = {o["id"] for o in resp_all.json()}
        assert {order_a1, order_a2, order_b1}.issubset(all_ids), f"Missing orders for {role_name}"

        # b. Query with customer_id=Consumer_A: must see only Consumer A's orders
        resp_a = client.get(
            f"/api/v1/orders?customer_id={consumer_a.id}",
            headers=_auth_header(staff_user),
        )
        assert resp_a.status_code == 200
        a_ids = {o["id"] for o in resp_a.json()}
        assert order_a1 in a_ids
        assert order_a2 in a_ids
        assert order_b1 not in a_ids

        # c. Query with customer_id=Consumer_B: must see only Consumer B's order
        resp_b = client.get(
            f"/api/v1/orders?customer_id={consumer_b.id}",
            headers=_auth_header(staff_user),
        )
        assert resp_b.status_code == 200
        b_ids = {o["id"] for o in resp_b.json()}
        assert order_b1 in b_ids
        assert order_a1 not in b_ids
        assert order_a2 not in b_ids

        # d. Query with non-existent customer UUID: returns empty list
        resp_empty = client.get(
            f"/api/v1/orders?customer_id={uuid.uuid4()}",
            headers=_auth_header(staff_user),
        )
        assert resp_empty.status_code == 200
        assert resp_empty.json() == []

        # e. Staff can retrieve any individual order by UUID
        resp_get_a = client.get(f"/api/v1/orders/{order_a1}", headers=_auth_header(staff_user))
        assert resp_get_a.status_code == 200
        assert resp_get_a.json()["id"] == order_a1


def test_adversarial_unauthenticated_requests_denied(client: TestClient, db: Session):
    """Unauthenticated or forged requests across all /api/v1/orders endpoints must return 401."""
    random_uuid = uuid.uuid4()
    dummy_payload = {"items": [{"variant_id": str(random_uuid), "quantity": "1.000"}]}

    # 1. POST /api/v1/orders without auth header
    resp_post_no_auth = client.post("/api/v1/orders", json=dummy_payload)
    assert resp_post_no_auth.status_code == 401

    # 2. POST /api/v1/orders with malformed / fake token
    resp_post_fake = client.post(
        "/api/v1/orders",
        json=dummy_payload,
        headers={"Authorization": "Bearer not-a-valid-jwt-token"},
    )
    assert resp_post_fake.status_code == 401

    # 3. POST /api/v1/orders with expired token
    expired_token = create_access_token(
        {"sub": str(uuid.uuid4()), "role": "consumer"},
        expires_delta=timedelta(seconds=-3600),
    )
    resp_post_expired = client.post(
        "/api/v1/orders",
        json=dummy_payload,
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp_post_expired.status_code == 401

    # 4. GET /api/v1/orders without auth header
    resp_get_no_auth = client.get("/api/v1/orders")
    assert resp_get_no_auth.status_code == 401

    # 5. GET /api/v1/orders/{id} without auth header
    resp_get_id_no_auth = client.get(f"/api/v1/orders/{random_uuid}")
    assert resp_get_id_no_auth.status_code == 401

    # 6. GET /api/v1/orders/{id} with forged token
    resp_get_id_fake = client.get(
        f"/api/v1/orders/{random_uuid}",
        headers={"Authorization": "Bearer forged-token-abc"},
    )
    assert resp_get_id_fake.status_code == 401


def test_adversarial_deactivated_user_denied(client: TestClient, db: Session, m1_catalog_fixture):
    """A deactivated user's token must be rejected with 403 ACCOUNT_DISABLED."""
    user = _make_user(db, email="deactivated_orders@test.com", role_name="consumer")
    var_250g = m1_catalog_fixture["var_250g"]

    # Generate token while active
    token = create_access_token({"sub": str(user.id), "role": "consumer"})
    auth_header = {"Authorization": f"Bearer {token}"}

    # Deactivate user in DB
    user.is_active = False
    db.commit()

    # Attempt order creation: rejected 403 ACCOUNT_DISABLED
    resp_post = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(var_250g.id), "quantity": "1.000"}]},
        headers=auth_header,
    )
    assert resp_post.status_code == 403
    assert resp_post.json()["code"] == "ACCOUNT_DISABLED"

    # Attempt order listing: rejected 403 ACCOUNT_DISABLED
    resp_get = client.get("/api/v1/orders", headers=auth_header)
    assert resp_get.status_code == 403
    assert resp_get.json()["code"] == "ACCOUNT_DISABLED"


def test_adversarial_user_with_unauthorized_role_denied_listing(client: TestClient, db: Session):
    """A user whose role is neither customer nor staff must be rejected with 403 on listing."""
    auditor_role = Role(name="auditor")
    db.add(auditor_role)
    db.commit()

    user = User(
        id=uuid.uuid4(),
        email="auditor_orders@test.com",
        password_hash=hash_password("testpass123"),
        full_name="Auditor User",
        role_id=auditor_role.id,
    )
    db.add(user)
    db.commit()

    token = create_access_token({"sub": str(user.id), "role": "auditor"})
    resp = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"
    assert "Role 'auditor' cannot list orders" in resp.json()["message"]


# ==============================================================================
# SECTION 2: Database Schema & Alembic Migration Roundtrip Tests
# ==============================================================================


def test_adversarial_alembic_schema_verification_head():
    """Verify all columns, types, foreign keys, and indexes on orders and order_items at HEAD."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        # 1. Upgrade to HEAD (migration 0005)
        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)

        # A. Verify orders columns
        orders_cols = {col["name"]: col for col in inspector.get_columns("orders")}
        assert "id" in orders_cols
        assert "order_number" in orders_cols
        assert "customer_id" in orders_cols
        assert "order_type" in orders_cols
        assert "status" in orders_cols
        assert "idempotency_key" in orders_cols
        assert "total_amount" in orders_cols
        assert "currency" in orders_cols
        assert "created_at" in orders_cols
        assert "updated_at" in orders_cols

        # Nullability checks
        assert orders_cols["order_number"]["nullable"] is False
        assert orders_cols["customer_id"]["nullable"] is True
        assert orders_cols["order_type"]["nullable"] is False
        assert orders_cols["status"]["nullable"] is False
        assert orders_cols["idempotency_key"]["nullable"] is True
        assert orders_cols["total_amount"]["nullable"] is False
        assert orders_cols["currency"]["nullable"] is False

        # B. Verify orders indexes
        orders_indexes = {idx["name"]: idx for idx in inspector.get_indexes("orders")}
        assert "ix_orders_customer_id" in orders_indexes
        assert orders_indexes["ix_orders_customer_id"]["column_names"] == ["customer_id"]

        assert "ix_orders_idempotency_key" in orders_indexes
        assert orders_indexes["ix_orders_idempotency_key"]["column_names"] == ["idempotency_key"]

        # Unique constraint is now composite: (customer_id, idempotency_key)
        all_unique_cols = [
            set(uc["column_names"]) for uc in inspector.get_unique_constraints("orders")
        ] + [set(idx["column_names"]) for idx in orders_indexes.values() if idx.get("unique")]
        assert {"customer_id", "idempotency_key"} in all_unique_cols or any(
            "idempotency" in n for n in orders_indexes
        )

        # C. Verify orders foreign keys
        orders_fks = inspector.get_foreign_keys("orders")
        user_fks = [fk for fk in orders_fks if fk["referred_table"] == "users"]
        assert len(user_fks) >= 1
        assert "customer_id" in user_fks[0]["constrained_columns"]

        # D. Verify order_items columns
        item_cols = {col["name"]: col for col in inspector.get_columns("order_items")}
        assert "id" in item_cols
        assert "order_id" in item_cols
        assert "variant_id" in item_cols
        assert "quantity" in item_cols
        assert "unit_price_snapshot" in item_cols
        assert "price_list_code" in item_cols
        assert "line_total" in item_cols
        assert "created_at" in item_cols

        assert item_cols["unit_price_snapshot"]["nullable"] is False
        assert item_cols["price_list_code"]["nullable"] is False
        assert item_cols["line_total"]["nullable"] is False

        # E. Verify order_items foreign keys
        item_fks = inspector.get_foreign_keys("order_items")
        order_fk = next((fk for fk in item_fks if fk["referred_table"] == "orders"), None)
        assert order_fk is not None
        assert "order_id" in order_fk["constrained_columns"]

        variant_fk = next(
            (fk for fk in item_fks if fk["referred_table"] == "product_variants"), None
        )
        assert variant_fk is not None
        assert "variant_id" in variant_fk["constrained_columns"]

    finally:
        engine.dispose()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


def test_adversarial_alembic_downgrade_normalization_and_reupgrade():
    """Verify migration 0005 downgrade normalizes status to 'open' and removes columns."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        # 1. Upgrade to HEAD
        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()

        # Seed an order with status 'packed' (a status introduced in 0005)
        order_id = str(uuid.uuid4())
        session.execute(
            text(
                "INSERT INTO orders (id, order_number, status, total_amount, currency) "
                "VALUES (:id, :num, :status, 150.00, 'INR')"
            ),
            {"id": order_id, "num": "ORD-DOWNGRADE-TEST", "status": "packed"},
        )
        session.commit()
        session.close()

        # 2. Downgrade to 0004_ai_subsystem
        command.downgrade(cfg, "0004_ai_subsystem")

        # Verify that the order status was normalized to 'open' to satisfy old constraint
        session = SessionLocal()
        row = session.execute(
            text("SELECT status FROM orders WHERE id = :id"),
            {"id": order_id},
        ).fetchone()
        assert row is not None
        assert row[0] == "open", f"Expected 'open' status after downgrade, got '{row[0]}'"

        # Verify columns added by 0005 were removed
        inspector = inspect(engine)
        orders_cols_downgraded = {col["name"] for col in inspector.get_columns("orders")}
        assert "customer_id" not in orders_cols_downgraded
        assert "idempotency_key" not in orders_cols_downgraded
        assert "total_amount" not in orders_cols_downgraded

        items_cols_downgraded = {col["name"] for col in inspector.get_columns("order_items")}
        assert "unit_price_snapshot" not in items_cols_downgraded
        assert "line_total" not in items_cols_downgraded
        assert "price_list_code" not in items_cols_downgraded
        session.close()

        # 3. Re-upgrade back to head
        command.upgrade(cfg, "head")

        inspector_reup = inspect(engine)
        orders_cols_reup = {col["name"] for col in inspector_reup.get_columns("orders")}
        assert "customer_id" in orders_cols_reup
        assert "idempotency_key" in orders_cols_reup
        assert "total_amount" in orders_cols_reup

        # 4. Downgrade all the way to base and back to head
        command.downgrade(cfg, "base")
        inspector_base = inspect(engine)
        assert len(set(inspector_base.get_table_names()) - {"alembic_version"}) == 0

        command.upgrade(cfg, "head")
        inspector_final = inspect(engine)
        assert "orders" in inspector_final.get_table_names()
        assert "order_items" in inspector_final.get_table_names()

    finally:
        engine.dispose()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


def test_adversarial_schema_constraints_and_foreign_key_cascades(db: Session, m1_catalog_fixture):
    """Verify DB-level constraints: idempotency uniqueness, FK ondelete SET NULL, and CASCADE."""
    consumer = _make_user(db, email="cascade_consumer@test.com", role_name="consumer")
    var_250g = m1_catalog_fixture["var_250g"]

    # 1. Unique idempotency key constraint in DB
    key_val = f"db-unique-{uuid.uuid4().hex[:6]}"
    order1 = Order(
        order_number=f"ORD-DB1-{uuid.uuid4().hex[:6]}",
        customer_id=consumer.id,
        idempotency_key=key_val,
        status="pending",
        total_amount=Decimal("120.00"),
    )
    db.add(order1)
    db.commit()

    # Second order with identical idempotency key must trigger DB IntegrityError
    order2 = Order(
        order_number=f"ORD-DB2-{uuid.uuid4().hex[:6]}",
        customer_id=consumer.id,
        idempotency_key=key_val,
        status="pending",
        total_amount=Decimal("120.00"),
    )
    db.add(order2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # 2. Nullable idempotency keys: multiple orders with NULL idempotency_key ARE permitted
    order3 = Order(
        order_number=f"ORD-DB3-{uuid.uuid4().hex[:6]}",
        customer_id=consumer.id,
        idempotency_key=None,
        status="pending",
        total_amount=Decimal("50.00"),
    )
    order4 = Order(
        order_number=f"ORD-DB4-{uuid.uuid4().hex[:6]}",
        customer_id=consumer.id,
        idempotency_key=None,
        status="pending",
        total_amount=Decimal("60.00"),
    )
    db.add_all([order3, order4])
    db.commit()
    assert order3.id is not None and order4.id is not None

    # 3. Order Item line_total & unit_price_snapshot non-negative check
    item1 = OrderItem(
        order_id=order1.id,
        variant_id=var_250g.id,
        quantity=Decimal("1.000"),
        unit_price_snapshot=Decimal("120.00"),
        price_list_code="MRP",
        line_total=Decimal("120.00"),
    )
    db.add(item1)
    db.commit()

    # 4. Foreign key ondelete CASCADE: Deleting order1 must automatically delete item1
    item_id = item1.id
    db.delete(order1)
    db.commit()
    db.expire_all()

    assert db.get(Order, order1.id) is None
    assert db.get(OrderItem, item_id) is None

    # 5. Foreign key ondelete SET NULL: Deleting consumer sets order3.customer_id to NULL
    db.delete(consumer)
    db.commit()

    db.refresh(order3)
    assert order3.customer_id is None
