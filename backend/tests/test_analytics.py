"""Tests for the sales analytics module (Milestone M6 / T-062).

The module is read-only: it reports over orders, order items, the catalog and
the stock balances. The tests pin down the numbers (including the exclusion of
cancelled orders), the grouping dimensions, the CSV export (which must agree
with the JSON endpoint) and the permissions.
"""

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.router import router as analytics_router
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.core.db import get_db
from app.core.errors import register_error_handlers
from app.inventory.models import InventoryBalance, Order, OrderItem, ProductionBatch, Warehouse
from tests.conftest import _make_user, _override_get_db


@pytest.fixture()
def client():
    """A test client with the analytics router mounted."""
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(analytics_router)
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers(db: Session):
    """Build a bearer token for a freshly created user of the given role."""

    def _headers(role_name: str) -> dict[str, str]:
        email = f"user-{role_name}-{uuid.uuid4().hex[:6]}@example.com"
        user = _make_user(db, email=email, role_name=role_name, password="TestPassword123!")
        token = create_access_token(
            {"sub": str(user.id), "role": user.role.name if user.role else ""}
        )
        return {"Authorization": f"Bearer {token}"}

    return _headers


def _seed_variant(
    db: Session,
    *,
    category_name: str,
    product_name: str,
    sku: str | None = None,
) -> tuple[ProductVariant, Product]:
    """Create a category, a product and one variant of it."""
    category = db.scalar(select(Category).where(Category.name == category_name))
    if category is None:
        category = Category(name=category_name)
        db.add(category)
        db.flush()
    product = Product(name=product_name, category_id=category.id)
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=sku or f"SKU-{uuid.uuid4().hex[:6]}",
        pack_size_g=100,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant, product


def _seed_order(
    db: Session,
    *,
    variant: ProductVariant,
    customer_id: uuid.UUID | None = None,
    quantity: Decimal = Decimal("10"),
    unit_price: Decimal = Decimal("25.50"),
    status: str = "pending",
    days_ago: int = 1,
) -> Order:
    """Create one order with a single priced line item."""
    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:8]}",
        customer_id=customer_id,
        order_type="sales",
        status=status,
        total_amount=quantity * unit_price,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            variant_id=variant.id,
            quantity=quantity,
            unit_price_snapshot=unit_price,
            price_list_code="MRP",
            line_total=quantity * unit_price,
        )
    )
    db.commit()
    return order


def _seed_batch_with_stock(
    db: Session,
    *,
    variant: ProductVariant,
    status: str = "released",
    expiry_in_days: int = 60,
    on_hand: Decimal = Decimal("50"),
) -> tuple[ProductionBatch, Warehouse]:
    """Create a batch with an inventory balance in a warehouse."""
    warehouse = Warehouse(code=f"WH-{uuid.uuid4().hex[:6]}", name="Central Warehouse")
    db.add(warehouse)
    db.flush()
    batch = ProductionBatch(
        variant_id=variant.id,
        batch_code=f"BATCH-{uuid.uuid4().hex[:8]}",
        manufactured_at=date.today() - timedelta(days=5),
        expiry_date=date.today() + timedelta(days=expiry_in_days),
        status=status,
    )
    db.add(batch)
    db.flush()
    db.add(
        InventoryBalance(
            warehouse_id=warehouse.id,
            batch_id=batch.id,
            on_hand=on_hand,
            reserved=Decimal("0"),
        )
    )
    db.commit()
    db.refresh(batch)
    return batch, warehouse


def test_sales_summary_totals_exclude_cancelled_orders(
    client: TestClient, db: Session, auth_headers
):
    """Revenue, units and orders exclude cancelled orders."""
    variant, _product = _seed_variant(
        db, category_name="Pure Spices", product_name="Turmeric Powder"
    )
    _seed_order(db, variant=variant, quantity=Decimal("10"), unit_price=Decimal("25.50"))
    _seed_order(
        db,
        variant=variant,
        quantity=Decimal("5"),
        unit_price=Decimal("100.00"),
        status="cancelled",
    )

    headers = auth_headers("owner")
    resp = client.get(
        "/api/v1/analytics/sales-summary?period=all_time&group_by=variant",
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_revenue"] == "255.00"
    assert body["total_units_sold"] == "10.000"
    assert body["total_order_count"] == 1
    assert body["total_line_count"] == 1
    assert body["group_count"] == 1
    assert body["groups"][0]["revenue"] == "255.00"
    assert body["groups"][0]["units_sold"] == "10.000"


def test_sales_summary_groups_by_variant_product_and_category(
    client: TestClient, db: Session, auth_headers
):
    """The three grouping dimensions aggregate correctly."""
    turmeric, turmeric_product = _seed_variant(
        db, category_name="Pure Spices", product_name="Turmeric Powder"
    )
    chilli, _chilli_product = _seed_variant(
        db,
        category_name="Pure Spices",
        product_name="Red Chilli Powder",
    )
    masala, _masala_product = _seed_variant(
        db, category_name="Blended Masalas", product_name="Garam Masala"
    )

    _seed_order(db, variant=turmeric, quantity=Decimal("10"), unit_price=Decimal("25.50"))
    _seed_order(db, variant=chilli, quantity=Decimal("2"), unit_price=Decimal("30.00"))
    _seed_order(db, variant=masala, quantity=Decimal("4"), unit_price=Decimal("50.00"))

    headers = auth_headers("owner")

    by_variant = client.get(
        "/api/v1/analytics/sales-summary?period=all_time&group_by=variant",
        headers=headers,
    ).json()
    assert by_variant["group_count"] == 3
    # Deterministic order: revenue descending.
    assert [group["revenue"] for group in by_variant["groups"]] == [
        "255.00",
        "200.00",
        "60.00",
    ]
    assert by_variant["groups"][0]["group_key"] == turmeric.sku
    assert by_variant["groups"][0]["label"] == turmeric_product.name

    by_product = client.get(
        "/api/v1/analytics/sales-summary?period=all_time&group_by=product",
        headers=headers,
    ).json()
    assert by_product["group_count"] == 3
    assert {group["group_key"] for group in by_product["groups"]} == {
        "Turmeric Powder",
        "Red Chilli Powder",
        "Garam Masala",
    }

    by_category = client.get(
        "/api/v1/analytics/sales-summary?period=all_time&group_by=category",
        headers=headers,
    ).json()
    assert by_category["group_count"] == 2
    categories = {group["group_key"]: group["revenue"] for group in by_category["groups"]}
    assert categories["Pure Spices"] == "315.00"
    assert categories["Blended Masalas"] == "200.00"


def test_csv_export_matches_the_json_endpoint(client: TestClient, db: Session, auth_headers):
    """The CSV download carries exactly the groups of the JSON response."""
    variant, _product = _seed_variant(
        db, category_name="Pure Spices", product_name="Turmeric Powder"
    )
    _seed_order(db, variant=variant, quantity=Decimal("10"), unit_price=Decimal("25.50"))
    _seed_order(db, variant=variant, quantity=Decimal("3"), unit_price=Decimal("12.00"))

    headers = auth_headers("owner")
    json_body = client.get(
        "/api/v1/analytics/sales-summary?period=all_time&group_by=variant",
        headers=headers,
    ).json()

    csv_resp = client.get(
        "/api/v1/analytics/sales-summary.csv?period=all_time&group_by=variant",
        headers=headers,
    )

    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_resp.headers["content-disposition"]
    assert "sales-summary" in csv_resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(csv_resp.text)))
    assert rows[0] == ["sku", "product_name", "revenue", "units_sold", "order_count", "line_count"]
    data_rows = rows[1:]
    assert len(data_rows) == len(json_body["groups"])

    for row, group in zip(data_rows, json_body["groups"], strict=True):
        assert row[0] == group["group_key"]
        assert row[1] == group["label"]
        assert row[2] == group["revenue"]
        assert row[3] == group["units_sold"]
        assert row[4] == str(group["order_count"])


def test_slow_movers_are_ordered_and_exclude_expired_and_held_batches(
    client: TestClient, db: Session, auth_headers
):
    """Slow movers are sorted by units sold and only count sellable stock."""
    fast, _fast_product = _seed_variant(
        db, category_name="Pure Spices", product_name="Turmeric Powder"
    )
    slow, _slow_product = _seed_variant(
        db, category_name="Pure Spices", product_name="Red Chilli Powder"
    )
    idle, _idle_product = _seed_variant(
        db, category_name="Blended Masalas", product_name="Garam Masala"
    )

    _seed_order(db, variant=fast, quantity=Decimal("10"), unit_price=Decimal("25.50"))
    _seed_order(db, variant=slow, quantity=Decimal("1"), unit_price=Decimal("30.00"))

    # The slow mover owns a sellable batch, a held batch and an expired batch.
    sellable, _warehouse = _seed_batch_with_stock(
        db, variant=slow, status="released", expiry_in_days=30, on_hand=Decimal("50")
    )
    _seed_batch_with_stock(
        db, variant=slow, status="on_hold", expiry_in_days=10, on_hand=Decimal("999")
    )
    _seed_batch_with_stock(
        db, variant=slow, status="released", expiry_in_days=-5, on_hand=Decimal("777")
    )
    _seed_batch_with_stock(
        db, variant=fast, status="released", expiry_in_days=20, on_hand=Decimal("10")
    )
    # The idle variant has stock but no sales at all: it is the slowest mover.
    _seed_batch_with_stock(
        db, variant=idle, status="released", expiry_in_days=40, on_hand=Decimal("5")
    )

    headers = auth_headers("owner")
    resp = client.get("/api/v1/analytics/slow-movers?period=all_time&limit=10", headers=headers)

    assert resp.status_code == 200
    items = resp.json()["items"]
    by_sku = {item["sku"]: item for item in items}

    assert by_sku[idle.sku]["units_sold"] == "0.000"
    assert by_sku[idle.sku]["available_stock"] == "5.000"
    assert by_sku[slow.sku]["units_sold"] == "1.000"
    assert by_sku[fast.sku]["units_sold"] == "10.000"
    # Lowest sales first.
    assert [item["sku"] for item in items] == [idle.sku, slow.sku, fast.sku]

    # Only the released, unexpired batch counts towards the available stock.
    assert by_sku[slow.sku]["available_stock"] == "50.000"
    assert by_sku[slow.sku]["earliest_expiring_batch_code"] == sellable.batch_code
    assert by_sku[slow.sku]["earliest_expiry_date"] == sellable.expiry_date.isoformat()


def test_analytics_endpoints_are_owner_only(client: TestClient, db: Session, auth_headers):
    """A dealer is forbidden on every analytics endpoint; the owner is allowed."""
    dealer_headers = auth_headers("dealer")
    owner_headers = auth_headers("owner")

    for url in (
        "/api/v1/analytics/sales-summary",
        "/api/v1/analytics/sales-summary.csv",
        "/api/v1/analytics/slow-movers",
    ):
        assert client.get(url, headers=dealer_headers).status_code == 403
        assert client.get(url, headers=owner_headers).status_code == 200


def test_analytics_requires_authentication(client: TestClient):
    """An anonymous caller gets 401, not data."""
    for url in (
        "/api/v1/analytics/sales-summary",
        "/api/v1/analytics/sales-summary.csv",
        "/api/v1/analytics/slow-movers",
    ):
        assert client.get(url).status_code == 401


def test_sales_summary_period_filter_ignores_old_orders(
    client: TestClient, db: Session, auth_headers
):
    """A period window only counts the orders inside it."""
    variant, _product = _seed_variant(
        db, category_name="Pure Spices", product_name="Turmeric Powder"
    )
    _seed_order(
        db, variant=variant, quantity=Decimal("10"), unit_price=Decimal("25.50"), days_ago=2
    )
    _seed_order(
        db,
        variant=variant,
        quantity=Decimal("7"),
        unit_price=Decimal("25.50"),
        status="delivered",
        days_ago=200,
    )

    headers = auth_headers("owner")
    body = client.get(
        "/api/v1/analytics/sales-summary?period=last_30_days&group_by=variant",
        headers=headers,
    ).json()

    assert body["total_order_count"] == 1
    assert body["total_units_sold"] == "10.000"
