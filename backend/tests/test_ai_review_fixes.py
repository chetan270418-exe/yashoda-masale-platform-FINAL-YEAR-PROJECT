"""Comprehensive regression tests for AI Subsystem review fixes (M7 & M8).

Verifies:
1. create_order_draft:
   - Role-based pricing (Rule B9: dealer -> DEALER, distributor -> DISTRIBUTOR, consumer -> MRP)
   - Rejection of unpriced variants
   - Rejection of empty items list
   - Rejection of inactive variants and inactive parent products
   - Rejection of unknown variants
   - Duplicate variant line merging (summing quantities into a single item)
   - Decimal string formatting for unit_price, line_total, total_estimated, and quantity (Rule B12)
2. get_sales_summary:
   - Period filtering (last_7_days, last_30_days, last_90_days, all_time)
   - Exclusion of cancelled orders
   - Grouping dimensions (variant, product, category)
   - Decimal string formatting for total_sold_quantity
   - RBAC enforcement (owner-only)
3. get_slow_movers:
   - threshold_days filtering (excluding variants with non-cancelled orders within threshold)
   - Inclusion of never-ordered or older-ordered variants
   - Exclusion of unreleased batches or zero-stock batches
   - Expiry date ascending ordering (FEFO)
   - Decimal string formatting for available quantity
   - RBAC enforcement (owner-only)
4. Rate limiting:
   - POST /api/v1/ai/chat (20 req/60s)
   - POST /api/v1/ai/agent/query (30 req/60s)
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import OrderDraft
from app.ai.router import ai_agent_rate_limiter, ai_chat_rate_limiter
from app.ai.tools import (
    ToolArgumentError,
    ToolPermissionError,
    execute_tool,
)
from app.auth.models import User
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.inventory.models import (
    InventoryBalance,
    Order,
    OrderItem,
    ProductionBatch,
    Warehouse,
)
from tests.conftest import _make_user

# ── Fixtures & Helpers ────────────────────────────────────────────────────────


@pytest.fixture
def owner_user(db: Session) -> User:
    return _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@example.com", role_name="owner")


@pytest.fixture
def dealer_user(db: Session) -> User:
    return _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")


@pytest.fixture
def distributor_user(db: Session) -> User:
    return _make_user(
        db, email=f"distributor-{uuid.uuid4().hex[:6]}@example.com", role_name="distributor"
    )


@pytest.fixture
def consumer_user(db: Session) -> User:
    return _make_user(
        db, email=f"consumer-{uuid.uuid4().hex[:6]}@example.com", role_name="consumer"
    )


@pytest.fixture
def auth_headers(client: TestClient, db: Session):
    def _headers(role_name: str) -> dict[str, str]:
        email = f"user-{role_name}-{uuid.uuid4().hex[:6]}@example.com"
        _make_user(db, email=email, role_name=role_name, password="TestPassword123!")
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPassword123!"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _headers


def _setup_variant_with_prices(
    db: Session,
    *,
    mrp: Decimal | None = None,
    dealer_price: Decimal | None = None,
    distributor_price: Decimal | None = None,
    is_variant_active: bool = True,
    is_product_active: bool = True,
) -> tuple[Product, ProductVariant]:
    """Helper: create Category, Product, Variant, and PriceListItems across price tiers."""
    cat = Category(name=f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(
        name=f"Product-{uuid.uuid4().hex[:6]}",
        category_id=cat.id,
        is_active=is_product_active,
    )
    db.add(prod)
    db.flush()

    var = ProductVariant(
        product_id=prod.id,
        sku=f"SKU-{uuid.uuid4().hex[:6]}",
        pack_size_g=250,
        is_active=is_variant_active,
    )
    db.add(var)
    db.flush()

    price_map = {
        "MRP": mrp,
        "DEALER": dealer_price,
        "DISTRIBUTOR": distributor_price,
    }
    for pl_name, price_val in price_map.items():
        if price_val is not None:
            pl = db.scalar(select(PriceList).where(PriceList.name == pl_name))
            assert pl is not None, f"Price list {pl_name} should be seeded"
            item = PriceListItem(
                price_list_id=pl.id,
                variant_id=var.id,
                price=price_val,
            )
            db.add(item)

    db.commit()
    db.refresh(prod)
    db.refresh(var)
    return prod, var


# ── 1. create_order_draft Tests ───────────────────────────────────────────────


def test_create_order_draft_role_based_pricing_dealer(db: Session, dealer_user: User):
    """Dealer user receives DEALER price list price (Rule B9)."""
    _, var = _setup_variant_with_prices(
        db,
        mrp=Decimal("150.00"),
        dealer_price=Decimal("120.00"),
        distributor_price=Decimal("100.00"),
    )

    res = execute_tool(
        db,
        dealer_user,
        "create_order_draft",
        {"items": [{"variant_id": str(var.id), "quantity": "5"}]},
    )
    assert res["status"] == "draft"
    assert res["requires_human_approval"] is True
    assert res["total_estimated"] == "600.00"

    item = res["items"][0]
    assert item["sku"] == var.sku
    assert item["quantity"] == "5"
    assert item["unit_price"] == "120.00"
    assert item["line_total"] == "600.00"


def test_create_order_draft_role_based_pricing_distributor(db: Session, distributor_user: User):
    """Distributor user receives DISTRIBUTOR price list price (Rule B9)."""
    _, var = _setup_variant_with_prices(
        db,
        mrp=Decimal("150.00"),
        dealer_price=Decimal("120.00"),
        distributor_price=Decimal("100.00"),
    )

    res = execute_tool(
        db,
        distributor_user,
        "create_order_draft",
        {"items": [{"variant_id": str(var.id), "quantity": "8"}]},
    )
    assert res["status"] == "draft"
    assert res["total_estimated"] == "800.00"

    item = res["items"][0]
    assert item["unit_price"] == "100.00"
    assert item["line_total"] == "800.00"


def test_create_order_draft_role_based_pricing_consumer(db: Session, consumer_user: User):
    """Consumer user receives MRP price list price (Rule B9)."""
    _, var = _setup_variant_with_prices(
        db,
        mrp=Decimal("150.00"),
        dealer_price=Decimal("120.00"),
        distributor_price=Decimal("100.00"),
    )

    res = execute_tool(
        db,
        consumer_user,
        "create_order_draft",
        {"items": [{"variant_id": str(var.id), "quantity": "2"}]},
    )
    assert res["status"] == "draft"
    assert res["total_estimated"] == "300.00"

    item = res["items"][0]
    assert item["unit_price"] == "150.00"
    assert item["line_total"] == "300.00"


def test_create_order_draft_rejects_empty_items(db: Session, dealer_user: User):
    """Empty items list must be rejected immediately."""
    with pytest.raises(ToolArgumentError) as exc_info:
        execute_tool(db, dealer_user, "create_order_draft", {"items": []})
    assert "cannot be empty" in str(exc_info.value).lower()


def test_create_order_draft_rejects_unknown_variant(db: Session, dealer_user: User):
    """Non-existent variant UUID must raise ToolArgumentError."""
    random_id = uuid.uuid4()
    with pytest.raises(ToolArgumentError) as exc_info:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(random_id), "quantity": "5"}]},
        )
    assert "not found" in str(exc_info.value).lower()


def test_create_order_draft_rejects_inactive_variant(db: Session, dealer_user: User):
    """Inactive variant must be rejected."""
    _, var = _setup_variant_with_prices(db, dealer_price=Decimal("100.00"), is_variant_active=False)
    with pytest.raises(ToolArgumentError) as exc_info:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var.id), "quantity": "5"}]},
        )
    assert "is inactive" in str(exc_info.value).lower()


def test_create_order_draft_rejects_inactive_product(db: Session, dealer_user: User):
    """Variant belonging to an inactive product must be rejected."""
    _, var = _setup_variant_with_prices(db, dealer_price=Decimal("100.00"), is_product_active=False)
    with pytest.raises(ToolArgumentError) as exc_info:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var.id), "quantity": "5"}]},
        )
    assert "is inactive" in str(exc_info.value).lower()


def test_create_order_draft_rejects_unpriced_variant(db: Session, dealer_user: User):
    """Variant with no active price in the caller's role price list must be rejected."""
    # Seed only MRP; dealer requires DEALER price list
    _, var = _setup_variant_with_prices(db, mrp=Decimal("150.00"), dealer_price=None)
    with pytest.raises(ToolArgumentError) as exc_info:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var.id), "quantity": "5"}]},
        )
    assert "no active price in price list 'dealer'" in str(exc_info.value).lower()


def test_create_order_draft_merges_duplicate_variant_lines(db: Session, dealer_user: User):
    """Items with identical variant_id must have quantities merged into one line."""
    _, var = _setup_variant_with_prices(db, dealer_price=Decimal("100.00"))

    res = execute_tool(
        db,
        dealer_user,
        "create_order_draft",
        {
            "items": [
                {"variant_id": str(var.id), "quantity": "10"},
                {"variant_id": str(var.id), "quantity": "15"},
            ]
        },
    )
    assert res["status"] == "draft"
    assert len(res["items"]) == 1
    item = res["items"][0]
    assert item["quantity"] == "25"
    assert item["unit_price"] == "100.00"
    assert item["line_total"] == "2500.00"
    assert res["total_estimated"] == "2500.00"

    # Verify persisted draft
    draft_record = db.scalar(select(OrderDraft).where(OrderDraft.id == uuid.UUID(res["draft_id"])))
    assert draft_record is not None
    assert len(draft_record.items) == 1
    assert draft_record.items[0]["quantity"] == "25"


def test_create_order_draft_decimal_string_types(db: Session, dealer_user: User):
    """All monetary and quantity fields in draft output must be strings (Rule B12)."""
    _, var = _setup_variant_with_prices(db, dealer_price=Decimal("45.50"))

    res = execute_tool(
        db,
        dealer_user,
        "create_order_draft",
        {"items": [{"variant_id": str(var.id), "quantity": "3"}]},
    )
    assert isinstance(res["total_estimated"], str)
    assert res["total_estimated"] == "136.50"

    item = res["items"][0]
    assert isinstance(item["quantity"], str)
    assert isinstance(item["unit_price"], str)
    assert isinstance(item["line_total"], str)
    assert item["unit_price"] == "45.50"
    assert item["line_total"] == "136.50"


# ── 2. get_sales_summary Tests ───────────────────────────────────────────────


def test_get_sales_summary_owner_only_rbac(db: Session, dealer_user: User, consumer_user: User):
    """Only owners can access sales summaries; others get ToolPermissionError."""
    with pytest.raises(ToolPermissionError):
        execute_tool(db, dealer_user, "get_sales_summary", {"period": "all_time"})

    with pytest.raises(ToolPermissionError):
        execute_tool(db, consumer_user, "get_sales_summary", {"period": "all_time"})


def test_get_sales_summary_excludes_cancelled_orders(db: Session, owner_user: User):
    """Cancelled orders must not be counted in sales summary totals."""
    _, var = _setup_variant_with_prices(db, mrp=Decimal("100.00"))

    # Non-cancelled order
    active_order = Order(order_number=f"ORD-ACT-{uuid.uuid4().hex[:6]}", status="shipped")
    db.add(active_order)
    db.flush()
    db.add(OrderItem(order_id=active_order.id, variant_id=var.id, quantity=Decimal("25.000")))

    # Cancelled order
    cancelled_order = Order(order_number=f"ORD-CAN-{uuid.uuid4().hex[:6]}", status="cancelled")
    db.add(cancelled_order)
    db.flush()
    db.add(OrderItem(order_id=cancelled_order.id, variant_id=var.id, quantity=Decimal("50.000")))
    db.commit()

    res = execute_tool(db, owner_user, "get_sales_summary", {"period": "all_time"})
    sales = res["sales"]
    target = next((s for s in sales if s["sku"] == var.sku), None)
    assert target is not None
    assert Decimal(target["total_sold_quantity"]) == Decimal("25.000")
    assert isinstance(target["total_sold_quantity"], str)


def test_get_sales_summary_period_filtering(db: Session, owner_user: User):
    """Period filter strictly limits orders by creation timestamp."""
    _, var = _setup_variant_with_prices(db, mrp=Decimal("100.00"))
    now = datetime.now(UTC)

    # 1. 3 days ago: 10 qty
    o1 = Order(
        order_number=f"ORD-P1-{uuid.uuid4().hex[:6]}",
        status="shipped",
        created_at=now - timedelta(days=3),
    )
    db.add(o1)
    db.flush()
    db.add(OrderItem(order_id=o1.id, variant_id=var.id, quantity=Decimal("10.000")))

    # 2. 15 days ago: 20 qty
    o2 = Order(
        order_number=f"ORD-P2-{uuid.uuid4().hex[:6]}",
        status="shipped",
        created_at=now - timedelta(days=15),
    )
    db.add(o2)
    db.flush()
    db.add(OrderItem(order_id=o2.id, variant_id=var.id, quantity=Decimal("20.000")))

    # 3. 60 days ago: 30 qty
    o3 = Order(
        order_number=f"ORD-P3-{uuid.uuid4().hex[:6]}",
        status="shipped",
        created_at=now - timedelta(days=60),
    )
    db.add(o3)
    db.flush()
    db.add(OrderItem(order_id=o3.id, variant_id=var.id, quantity=Decimal("30.000")))

    # 4. 120 days ago: 40 qty
    o4 = Order(
        order_number=f"ORD-P4-{uuid.uuid4().hex[:6]}",
        status="shipped",
        created_at=now - timedelta(days=120),
    )
    db.add(o4)
    db.flush()
    db.add(OrderItem(order_id=o4.id, variant_id=var.id, quantity=Decimal("40.000")))
    db.commit()

    def _get_qty_for_sku(period: str) -> Decimal:
        res = execute_tool(db, owner_user, "get_sales_summary", {"period": period})
        target = next((s for s in res["sales"] if s["sku"] == var.sku), None)
        return Decimal(target["total_sold_quantity"]) if target else Decimal("0")

    assert _get_qty_for_sku("last_7_days") == Decimal("10.000")
    assert _get_qty_for_sku("last_30_days") == Decimal("30.000")
    assert _get_qty_for_sku("last_90_days") == Decimal("60.000")
    assert _get_qty_for_sku("all_time") == Decimal("100.000")


def test_get_sales_summary_group_by_dimensions(db: Session, owner_user: User):
    """Supports group_by dimensions: variant, product, and category."""
    cat1 = Category(name=f"Cat-Masala-{uuid.uuid4().hex[:6]}")
    cat2 = Category(name=f"Cat-Pure-{uuid.uuid4().hex[:6]}")
    db.add_all([cat1, cat2])
    db.flush()

    p1 = Product(name=f"Product-Garam-{uuid.uuid4().hex[:6]}", category_id=cat1.id)
    p2 = Product(name=f"Product-Turmeric-{uuid.uuid4().hex[:6]}", category_id=cat2.id)
    db.add_all([p1, p2])
    db.flush()

    v1_100g = ProductVariant(
        product_id=p1.id, sku=f"GM-100G-{uuid.uuid4().hex[:6]}", pack_size_g=100
    )
    v1_500g = ProductVariant(
        product_id=p1.id, sku=f"GM-500G-{uuid.uuid4().hex[:6]}", pack_size_g=500
    )
    v2_200g = ProductVariant(
        product_id=p2.id, sku=f"TM-200G-{uuid.uuid4().hex[:6]}", pack_size_g=200
    )
    db.add_all([v1_100g, v1_500g, v2_200g])
    db.flush()

    order = Order(order_number=f"ORD-GRP-{uuid.uuid4().hex[:6]}", status="shipped")
    db.add(order)
    db.flush()

    db.add(OrderItem(order_id=order.id, variant_id=v1_100g.id, quantity=Decimal("10.000")))
    db.add(OrderItem(order_id=order.id, variant_id=v1_500g.id, quantity=Decimal("15.000")))
    db.add(OrderItem(order_id=order.id, variant_id=v2_200g.id, quantity=Decimal("25.000")))
    db.commit()

    # 1. Group by variant
    res_var = execute_tool(
        db, owner_user, "get_sales_summary", {"period": "all_time", "group_by": "variant"}
    )
    sales_var = {s["sku"]: Decimal(s["total_sold_quantity"]) for s in res_var["sales"]}
    assert sales_var[v1_100g.sku] == Decimal("10.000")
    assert sales_var[v1_500g.sku] == Decimal("15.000")
    assert sales_var[v2_200g.sku] == Decimal("25.000")

    # 2. Group by product
    res_prod = execute_tool(
        db, owner_user, "get_sales_summary", {"period": "all_time", "group_by": "product"}
    )
    sales_prod = {s["product_name"]: Decimal(s["total_sold_quantity"]) for s in res_prod["sales"]}
    assert sales_prod[p1.name] == Decimal("25.000")
    assert sales_prod[p2.name] == Decimal("25.000")

    # 3. Group by category
    res_cat = execute_tool(
        db, owner_user, "get_sales_summary", {"period": "all_time", "group_by": "category"}
    )
    sales_cat = {s["category_name"]: Decimal(s["total_sold_quantity"]) for s in res_cat["sales"]}
    assert sales_cat[cat1.name] == Decimal("25.000")
    assert sales_cat[cat2.name] == Decimal("25.000")


# ── 3. get_slow_movers Tests ──────────────────────────────────────────────────


def test_get_slow_movers_owner_only_rbac(db: Session, dealer_user: User):
    """Non-owner calling get_slow_movers raises ToolPermissionError."""
    with pytest.raises(ToolPermissionError):
        execute_tool(db, dealer_user, "get_slow_movers", {"threshold_days": 30})


def test_get_slow_movers_threshold_and_fefo_expiry_sort(db: Session, owner_user: User):
    """Slow movers finds released stock for variants with no recent orders, sorted by expiry."""
    wh = Warehouse(code=f"WH-{uuid.uuid4().hex[:6]}", name="Central Warehouse")
    db.add(wh)
    db.flush()

    _, var_active = _setup_variant_with_prices(db, mrp=Decimal("50.00"))  # has order in 5 days
    _, var_slow = _setup_variant_with_prices(db, mrp=Decimal("60.00"))  # order 45 days ago
    _, var_never = _setup_variant_with_prices(db, mrp=Decimal("70.00"))  # never ordered

    now = datetime.now(UTC)
    today = date.today()

    # Active order 5 days ago for var_active
    o_active = Order(
        order_number=f"ORD-ACT-{uuid.uuid4().hex[:6]}",
        status="shipped",
        created_at=now - timedelta(days=5),
    )
    db.add(o_active)
    db.flush()
    db.add(OrderItem(order_id=o_active.id, variant_id=var_active.id, quantity=Decimal("5.000")))

    # Old order 45 days ago for var_slow
    o_old = Order(
        order_number=f"ORD-OLD-{uuid.uuid4().hex[:6]}",
        status="shipped",
        created_at=now - timedelta(days=45),
    )
    db.add(o_old)
    db.flush()
    db.add(OrderItem(order_id=o_old.id, variant_id=var_slow.id, quantity=Decimal("5.000")))

    # Batches:
    # B1 for var_active: should be EXCLUDED (active order within 30 days)
    b1 = ProductionBatch(
        variant_id=var_active.id,
        batch_code=f"B-ACT-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=today + timedelta(days=180),
    )
    # B2 for var_slow: should be INCLUDED (later expiry)
    b2 = ProductionBatch(
        variant_id=var_slow.id,
        batch_code=f"B-SLOW-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=today + timedelta(days=120),
    )
    # B3 for var_never: should be INCLUDED (earlier expiry)
    b3 = ProductionBatch(
        variant_id=var_never.id,
        batch_code=f"B-NEVER-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=today + timedelta(days=60),
    )
    # B4 for var_never: held -> should be EXCLUDED
    b4 = ProductionBatch(
        variant_id=var_never.id,
        batch_code=f"B-HELD-{uuid.uuid4().hex[:6]}",
        status="hold",
        expiry_date=today + timedelta(days=30),
    )
    # B5 for var_slow: available = 0 -> should be EXCLUDED
    b5 = ProductionBatch(
        variant_id=var_slow.id,
        batch_code=f"B-ZERO-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=today + timedelta(days=40),
    )
    db.add_all([b1, b2, b3, b4, b5])
    db.flush()

    # Inventory balances
    db.add(
        InventoryBalance(
            warehouse_id=wh.id, batch_id=b1.id, on_hand=Decimal("50.000"), reserved=Decimal("0.000")
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id, batch_id=b2.id, on_hand=Decimal("40.000"), reserved=Decimal("0.000")
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id,
            batch_id=b3.id,
            on_hand=Decimal("30.000"),
            reserved=Decimal("0.000"),
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id,
            batch_id=b4.id,
            on_hand=Decimal("20.000"),
            reserved=Decimal("0.000"),
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id,
            batch_id=b5.id,
            on_hand=Decimal("10.000"),
            reserved=Decimal("10.000"),
        )
    )
    db.commit()

    res = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 30})
    slow_batches = res["slow_movers"]
    batch_codes = [s["batch_code"] for s in slow_batches]

    # B3 (earlier expiry) must appear before B2 (later expiry)
    assert b3.batch_code in batch_codes
    assert b2.batch_code in batch_codes
    assert batch_codes.index(b3.batch_code) < batch_codes.index(b2.batch_code)

    # Excluded batches
    assert b1.batch_code not in batch_codes  # active within 30 days
    assert b4.batch_code not in batch_codes  # unreleased/hold
    assert b5.batch_code not in batch_codes  # available = 0

    # Verify decimal string formatting
    target_b3 = next(s for s in slow_batches if s["batch_code"] == b3.batch_code)
    assert isinstance(target_b3["available"], str)
    assert Decimal(target_b3["available"]) == Decimal("30.000")


# ── 4. Rate Limiting Tests ────────────────────────────────────────────────────


def test_ai_chat_rate_limiting(client: TestClient):
    """POST /api/v1/ai/chat allows 20 req/60s, then returns HTTP 429."""
    ai_chat_rate_limiter.reset()

    # First 20 requests succeed
    for i in range(20):
        resp = client.post(
            "/api/v1/ai/chat",
            json={"query": f"Question {i}: What is the curcumin percentage?"},
        )
        assert resp.status_code == 200, f"Request {i + 1} failed with {resp.status_code}"

    # 21st request receives HTTP 429
    blocked = client.post(
        "/api/v1/ai/chat",
        json={"query": "Question 21: Are we rate limited?"},
    )
    assert blocked.status_code == 429
    err_body = blocked.json()
    assert err_body["code"] == "RATE_LIMIT_EXCEEDED"

    # Resetting limiter restores immediate access
    ai_chat_rate_limiter.reset()
    restored = client.post(
        "/api/v1/ai/chat",
        json={"query": "Question after reset: Should succeed."},
    )
    assert restored.status_code == 200


def test_ai_agent_query_rate_limiting(client: TestClient, auth_headers):
    """POST /api/v1/ai/agent/query allows 30 req/60s, then returns HTTP 429."""
    ai_agent_rate_limiter.reset()
    headers = auth_headers("dealer")

    # First 30 requests succeed
    for i in range(30):
        resp = client.post(
            "/api/v1/ai/agent/query",
            json={"prompt": f"Request {i}: check stock of turmeric"},
            headers=headers,
        )
        assert resp.status_code == 200, f"Request {i + 1} failed with {resp.status_code}"

    # 31st request receives HTTP 429
    blocked = client.post(
        "/api/v1/ai/agent/query",
        json={"prompt": "Request 31: check stock"},
        headers=headers,
    )
    assert blocked.status_code == 429
    err_body = blocked.json()
    assert err_body["code"] == "RATE_LIMIT_EXCEEDED"

    # Resetting limiter restores immediate access
    ai_agent_rate_limiter.reset()
    restored = client.post(
        "/api/v1/ai/agent/query",
        json={"prompt": "Request after reset: check stock"},
        headers=headers,
    )
    assert restored.status_code == 200
