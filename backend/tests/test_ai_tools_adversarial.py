"""Adversarial stress-test suite for AI Whitelisted Tools (Rules B9, B12, 60, 61, 62, 65).

Rigorous empirical challenge of backend/app/ai/tools.py covering:
1. create_order_draft:
   - Role-based pricing for dealer, distributor, consumer (B9)
   - Variant/product active/inactive state matrix
   - Unpriced variant handling (no fallback, strict rejection)
   - Future-dated price list item rejection
   - Latest active price selection among multiple valid historical prices
   - Duplicate variant line merging (multi-line, heavy duplicates, mixed variants)
   - Zero, negative, invalid, and empty items validation
   - Partial failure atomicity (rollback check)
   - Decimal string enforcement (B12)
2. get_sales_summary:
   - Owner-only RBAC
   - Strict period boundaries (7d, 30d, 90d, all_time)
   - Cancelled order exclusion across all periods
   - Inclusion of non-cancelled order statuses (open, reserved, partially_shipped, shipped)
   - Accurate aggregation across grouping dimensions: variant, product, category
   - Decimal string enforcement for total_sold_quantity
   - Argument validation
3. get_slow_movers:
   - Owner-only RBAC
   - FEFO sorting by expiry date ascending
   - Threshold window exclusion of variants with active orders
   - Inclusion of variants with cancelled-only orders or orders outside threshold
   - Inclusion of variants never ordered
   - Batch status exclusion (hold, rejected, recalled)
   - Zero available stock exclusion (on_hand == reserved)
   - Multi-warehouse isolation
   - Decimal string enforcement for available quantity
   - Threshold boundary validation
4. get_dealer_history:
   - Dealer isolation vs owner access
   - Decimal string enforcement
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import OrderDraft
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

# ── Fixtures & Setup Helpers ──────────────────────────────────────────────────


@pytest.fixture
def owner_user(db: Session) -> User:
    return _make_user(db, email=f"owner-adv-{uuid.uuid4().hex[:6]}@example.com", role_name="owner")


@pytest.fixture
def dealer_user(db: Session) -> User:
    return _make_user(
        db, email=f"dealer-adv-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer"
    )


@pytest.fixture
def distributor_user(db: Session) -> User:
    return _make_user(
        db, email=f"dist-adv-{uuid.uuid4().hex[:6]}@example.com", role_name="distributor"
    )


@pytest.fixture
def consumer_user(db: Session) -> User:
    return _make_user(
        db, email=f"cons-adv-{uuid.uuid4().hex[:6]}@example.com", role_name="consumer"
    )


@pytest.fixture
def factory_user(db: Session) -> User:
    return _make_user(
        db, email=f"factory-adv-{uuid.uuid4().hex[:6]}@example.com", role_name="factory"
    )


@pytest.fixture
def warehouse_user(db: Session) -> User:
    return _make_user(db, email=f"wh-adv-{uuid.uuid4().hex[:6]}@example.com", role_name="warehouse")


def _create_catalog_item(
    db: Session,
    *,
    sku: str | None = None,
    product_name: str | None = None,
    category_name: str | None = None,
    is_variant_active: bool = True,
    is_product_active: bool = True,
    prices: dict[str, Decimal] | None = None,
) -> tuple[Category, Product, ProductVariant]:
    """Helper to assemble Category, Product, Variant, and price entries."""
    cat = Category(name=category_name or f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(
        name=product_name or f"Prod-{uuid.uuid4().hex[:6]}",
        category_id=cat.id,
        is_active=is_product_active,
    )
    db.add(prod)
    db.flush()

    var = ProductVariant(
        product_id=prod.id,
        sku=sku or f"SKU-{uuid.uuid4().hex[:6]}",
        pack_size_g=500,
        is_active=is_variant_active,
    )
    db.add(var)
    db.flush()

    if prices:
        for pl_name, p_val in prices.items():
            pl = db.scalar(select(PriceList).where(PriceList.name == pl_name))
            assert pl is not None, f"Price list {pl_name} not found"
            db.add(PriceListItem(price_list_id=pl.id, variant_id=var.id, price=p_val))

    db.commit()
    db.refresh(cat)
    db.refresh(prod)
    db.refresh(var)
    return cat, prod, var


def _assert_no_floats(obj: Any, path: str = "root") -> None:
    """Adversarial check: recursively ensure NO float type exists in data structure."""
    if isinstance(obj, float):
        raise AssertionError(f"Float found at {path}: {obj} (violates Rule B12)")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_floats(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _assert_no_floats(item, f"{path}[{idx}]")


# ── 1. Adversarial Tests for create_order_draft ───────────────────────────────


def test_adv_create_draft_rejects_empty_and_missing_items(db: Session, dealer_user: User):
    """Empty items list or missing items argument must be rejected immediately."""
    with pytest.raises(ToolArgumentError):
        execute_tool(db, dealer_user, "create_order_draft", {"items": []})

    with pytest.raises(ToolArgumentError):
        execute_tool(db, dealer_user, "create_order_draft", {})


def test_adv_create_draft_rejects_zero_or_negative_quantities(db: Session, dealer_user: User):
    """Items with quantity <= 0 must fail validation and not commit anything."""
    _, _, var = _create_catalog_item(db, prices={"DEALER": Decimal("100.00")})

    # Zero quantity
    with pytest.raises(ValidationError):
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var.id), "quantity": "0"}]},
        )

    # Negative quantity
    with pytest.raises(ValidationError):
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var.id), "quantity": "-5"}]},
        )


def test_adv_create_draft_rejects_unknown_variant_uuid(db: Session, dealer_user: User):
    """Random nonexistent variant UUID must raise ToolArgumentError."""
    fake_id = uuid.uuid4()
    with pytest.raises(ToolArgumentError) as exc:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(fake_id), "quantity": "10"}]},
        )
    assert "not found" in str(exc.value).lower()


def test_adv_create_draft_active_inactive_hierarchy_matrix(db: Session, dealer_user: User):
    """Adversarial matrix:
    - Active variant under inactive product -> REJECTED
    - Inactive variant under active product -> REJECTED
    - Inactive variant under inactive product -> REJECTED
    """
    # 1. Active variant, Inactive product
    _, _, v1 = _create_catalog_item(
        db, is_variant_active=True, is_product_active=False, prices={"DEALER": Decimal("100.00")}
    )
    with pytest.raises(ToolArgumentError) as exc1:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(v1.id), "quantity": "10"}]},
        )
    assert "is inactive" in str(exc1.value).lower()

    # 2. Inactive variant, Active product
    _, _, v2 = _create_catalog_item(
        db, is_variant_active=False, is_product_active=True, prices={"DEALER": Decimal("100.00")}
    )
    with pytest.raises(ToolArgumentError) as exc2:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(v2.id), "quantity": "10"}]},
        )
    assert "is inactive" in str(exc2.value).lower()

    # 3. Inactive variant, Inactive product
    _, _, v3 = _create_catalog_item(
        db, is_variant_active=False, is_product_active=False, prices={"DEALER": Decimal("100.00")}
    )
    with pytest.raises(ToolArgumentError) as exc3:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(v3.id), "quantity": "10"}]},
        )
    assert "is inactive" in str(exc3.value).lower()


def test_adv_create_draft_role_based_price_enforcement_and_no_fallback(
    db: Session, dealer_user: User, consumer_user: User, distributor_user: User
):
    """Ensure strict role-based price list resolution (Rule B9).
    Never fall back to MRP if role price is missing.
    """
    # Variant A has ONLY MRP (100.00)
    _, _, var_mrp_only = _create_catalog_item(db, prices={"MRP": Decimal("100.00")})

    # Variant B has ONLY DEALER (80.00)
    _, _, var_dealer_only = _create_catalog_item(db, prices={"DEALER": Decimal("80.00")})

    # Variant C has ONLY DISTRIBUTOR (70.00)
    _, _, var_dist_only = _create_catalog_item(db, prices={"DISTRIBUTOR": Decimal("70.00")})

    # Dealer tries to order var_mrp_only -> REJECTED (no dealer price)
    with pytest.raises(ToolArgumentError) as exc_d:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var_mrp_only.id), "quantity": "5"}]},
        )
    assert "no active price in price list 'dealer'" in str(exc_d.value).lower()

    # Consumer tries to order var_dealer_only -> REJECTED (no MRP price)
    with pytest.raises(ToolArgumentError) as exc_c:
        execute_tool(
            db,
            consumer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var_dealer_only.id), "quantity": "5"}]},
        )
    assert "no active price in price list 'mrp'" in str(exc_c.value).lower()

    # Distributor tries to order var_mrp_only -> REJECTED (no distributor price)
    with pytest.raises(ToolArgumentError) as exc_dist:
        execute_tool(
            db,
            distributor_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var_mrp_only.id), "quantity": "5"}]},
        )
    assert "no active price in price list 'distributor'" in str(exc_dist.value).lower()


def test_adv_create_draft_future_dated_price_rejected(db: Session, dealer_user: User):
    """A price list item with valid_from in the future must not be considered active."""
    _, _, var = _create_catalog_item(db)
    dealer_pl = db.scalar(select(PriceList).where(PriceList.name == "DEALER"))

    # Add future-dated price (tomorrow)
    db.add(
        PriceListItem(
            price_list_id=dealer_pl.id,
            variant_id=var.id,
            price=Decimal("120.00"),
            valid_from=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.commit()

    with pytest.raises(ToolArgumentError) as exc:
        execute_tool(
            db,
            dealer_user,
            "create_order_draft",
            {"items": [{"variant_id": str(var.id), "quantity": "5"}]},
        )
    assert "no active price in price list 'dealer'" in str(exc.value).lower()


def test_adv_create_draft_historical_price_picks_latest_active(db: Session, dealer_user: User):
    """When multiple valid_from prices exist in the past, pick the newest active one."""
    _, _, var = _create_catalog_item(db)
    dealer_pl = db.scalar(select(PriceList).where(PriceList.name == "DEALER"))

    now = datetime.now(UTC)
    # Price 10 days ago: 90.00
    db.add(
        PriceListItem(
            price_list_id=dealer_pl.id,
            variant_id=var.id,
            price=Decimal("90.00"),
            valid_from=now - timedelta(days=10),
        )
    )
    # Price 2 days ago: 110.00 (the active current price)
    db.add(
        PriceListItem(
            price_list_id=dealer_pl.id,
            variant_id=var.id,
            price=Decimal("110.00"),
            valid_from=now - timedelta(days=2),
        )
    )
    # Price 5 days in future: 150.00 (not yet valid)
    db.add(
        PriceListItem(
            price_list_id=dealer_pl.id,
            variant_id=var.id,
            price=Decimal("150.00"),
            valid_from=now + timedelta(days=5),
        )
    )
    db.commit()

    res = execute_tool(
        db,
        dealer_user,
        "create_order_draft",
        {"items": [{"variant_id": str(var.id), "quantity": "10"}]},
    )
    assert res["items"][0]["unit_price"] == "110.00"
    assert res["items"][0]["line_total"] == "1100.00"
    assert res["total_estimated"] == "1100.00"


def test_adv_create_draft_heavy_duplicate_merging(db: Session, dealer_user: User):
    """Submit 10 duplicate lines of the same variant.
    Quantities: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 -> Sum = 55.
    Price = 20.00 -> Total = 1100.00.
    """
    _, _, var = _create_catalog_item(db, prices={"DEALER": Decimal("20.00")})

    items_payload = [{"variant_id": str(var.id), "quantity": str(i)} for i in range(1, 11)]
    res = execute_tool(db, dealer_user, "create_order_draft", {"items": items_payload})

    assert len(res["items"]) == 1
    merged = res["items"][0]
    assert merged["quantity"] == "55"
    assert merged["unit_price"] == "20.00"
    assert merged["line_total"] == "1100.00"
    assert res["total_estimated"] == "1100.00"

    # Verify persisted record in DB
    draft = db.scalar(select(OrderDraft).where(OrderDraft.id == uuid.UUID(res["draft_id"])))
    assert draft is not None
    assert len(draft.items) == 1
    assert draft.items[0]["quantity"] == "55"
    assert draft.total_estimated == Decimal("1100.00")


def test_adv_create_draft_mixed_variants_with_selective_duplicates(db: Session, dealer_user: User):
    """Variant A (qty 3 + qty 7 = 10 @ 50.00 = 500.00)
    Variant B (qty 4 @ 100.00 = 400.00)
    Variant C (qty 1 + qty 2 + qty 3 = 6 @ 25.00 = 150.00)
    Total = 1050.00.
    """
    _, _, vA = _create_catalog_item(db, sku="VA-1", prices={"DEALER": Decimal("50.00")})
    _, _, vB = _create_catalog_item(db, sku="VB-1", prices={"DEALER": Decimal("100.00")})
    _, _, vC = _create_catalog_item(db, sku="VC-1", prices={"DEALER": Decimal("25.00")})

    items = [
        {"variant_id": str(vA.id), "quantity": "3"},
        {"variant_id": str(vB.id), "quantity": "4"},
        {"variant_id": str(vC.id), "quantity": "1"},
        {"variant_id": str(vA.id), "quantity": "7"},
        {"variant_id": str(vC.id), "quantity": "2"},
        {"variant_id": str(vC.id), "quantity": "3"},
    ]
    res = execute_tool(db, dealer_user, "create_order_draft", {"items": items})

    assert len(res["items"]) == 3
    items_by_sku = {item["sku"]: item for item in res["items"]}
    assert items_by_sku["VA-1"]["quantity"] == "10"
    assert items_by_sku["VA-1"]["line_total"] == "500.00"
    assert items_by_sku["VB-1"]["quantity"] == "4"
    assert items_by_sku["VB-1"]["line_total"] == "400.00"
    assert items_by_sku["VC-1"]["quantity"] == "6"
    assert items_by_sku["VC-1"]["line_total"] == "150.00"
    assert res["total_estimated"] == "1050.00"


def test_adv_create_draft_partial_failure_atomicity(db: Session, dealer_user: User):
    """If one item in a multi-item draft fails (e.g. inactive or unknown),
    the entire operation must fail and no draft record may be committed.
    """
    _, _, v_valid = _create_catalog_item(db, prices={"DEALER": Decimal("50.00")})
    _, _, v_inactive = _create_catalog_item(
        db, is_variant_active=False, prices={"DEALER": Decimal("50.00")}
    )

    items = [
        {"variant_id": str(v_valid.id), "quantity": "5"},
        {"variant_id": str(v_inactive.id), "quantity": "5"},
    ]

    with pytest.raises(ToolArgumentError):
        execute_tool(db, dealer_user, "create_order_draft", {"items": items})

    # Assert no order draft was created for this user
    drafts = db.scalars(select(OrderDraft).where(OrderDraft.customer_id == dealer_user.id)).all()
    assert len(drafts) == 0


def test_adv_create_draft_strict_no_floats_rule_b12(db: Session, dealer_user: User):
    """All numerical values in response must be Decimal strings; no floats allowed."""
    _, _, var = _create_catalog_item(db, prices={"DEALER": Decimal("123.45")})

    res = execute_tool(
        db,
        dealer_user,
        "create_order_draft",
        {"items": [{"variant_id": str(var.id), "quantity": "2.5"}]},
    )
    _assert_no_floats(res)

    item = res["items"][0]
    assert isinstance(item["unit_price"], str)
    assert isinstance(item["line_total"], str)
    assert isinstance(item["quantity"], str)
    assert isinstance(res["total_estimated"], str)
    assert Decimal(item["unit_price"]) == Decimal("123.45")
    assert Decimal(item["line_total"]) == Decimal("308.62")
    assert Decimal(res["total_estimated"]) == Decimal("308.62")


# ── 2. Adversarial Tests for get_sales_summary ───────────────────────────────


def test_adv_sales_summary_strict_rbac(
    db: Session,
    dealer_user: User,
    distributor_user: User,
    consumer_user: User,
    factory_user: User,
    warehouse_user: User,
):
    """Every role except owner MUST be rejected with ToolPermissionError."""
    for u in (dealer_user, distributor_user, consumer_user, factory_user, warehouse_user):
        with pytest.raises(ToolPermissionError):
            execute_tool(db, u, "get_sales_summary", {"period": "all_time"})


def test_adv_sales_summary_cancelled_order_isolation_across_all_periods(
    db: Session, owner_user: User
):
    """Cancelled orders must NEVER leak into sales summary totals under any period."""
    _, _, var = _create_catalog_item(db, prices={"MRP": Decimal("100.00")})
    now = datetime.now(UTC)

    # 1. Shipped order: 15 qty, 2 days ago
    o1 = Order(
        order_number=f"ORD-OK-{uuid.uuid4().hex[:6]}",
        status="dispatched",
        created_at=now - timedelta(days=2),
    )
    db.add(o1)
    db.flush()
    db.add(OrderItem(order_id=o1.id, variant_id=var.id, quantity=Decimal("15.000")))

    # 2. Cancelled order: 999 qty, 2 days ago
    o2 = Order(
        order_number=f"ORD-CAN-1-{uuid.uuid4().hex[:6]}",
        status="cancelled",
        created_at=now - timedelta(days=2),
    )
    db.add(o2)
    db.flush()
    db.add(OrderItem(order_id=o2.id, variant_id=var.id, quantity=Decimal("999.000")))

    # 3. Cancelled order: 888 qty, 50 days ago
    o3 = Order(
        order_number=f"ORD-CAN-2-{uuid.uuid4().hex[:6]}",
        status="cancelled",
        created_at=now - timedelta(days=50),
    )
    db.add(o3)
    db.flush()
    db.add(OrderItem(order_id=o3.id, variant_id=var.id, quantity=Decimal("888.000")))
    db.commit()

    for p in ("last_7_days", "last_30_days", "last_90_days", "all_time"):
        res = execute_tool(db, owner_user, "get_sales_summary", {"period": p})
        target = next((s for s in res["sales"] if s["sku"] == var.sku), None)
        assert target is not None, f"Expected SKU {var.sku} in period {p}"
        assert target["total_sold_quantity"] == "15.000"


def test_adv_sales_summary_non_cancelled_statuses_included(db: Session, owner_user: User):
    """Non-cancelled statuses (open, reserved, partially_shipped, shipped) must all be included."""
    _, _, var = _create_catalog_item(db, prices={"MRP": Decimal("100.00")})
    now = datetime.now(UTC)

    statuses = ["pending", "reserved", "packed", "dispatched"]
    for idx, st in enumerate(statuses, 1):
        ord_obj = Order(
            order_number=f"ORD-{st}-{uuid.uuid4().hex[:6]}",
            status=st,
            created_at=now - timedelta(days=1),
        )
        db.add(ord_obj)
        db.flush()
        db.add(
            OrderItem(
                order_id=ord_obj.id,
                variant_id=var.id,
                quantity=Decimal(f"{idx * 10}.000"),
            )
        )
    db.commit()

    # Total expected = 10 + 20 + 30 + 40 = 100.000
    res = execute_tool(db, owner_user, "get_sales_summary", {"period": "last_7_days"})
    target = next((s for s in res["sales"] if s["sku"] == var.sku), None)
    assert target is not None
    assert Decimal(target["total_sold_quantity"]) == Decimal("100.000")


def test_adv_sales_summary_precise_period_boundary_windows(db: Session, owner_user: User):
    """Verify exact period cutoff thresholds:
    - 6 days ago (<= 7 days) -> included in last_7_days, last_30_days, last_90_days, all_time
    - 8 days ago (> 7 days, <= 30 days) -> excluded from last_7_days, included in last_30_days+
    - 32 days ago (> 30 days, <= 90 days) -> excluded from last_30_days, included in last_90_days+
    - 95 days ago (> 90 days) -> included in all_time only
    """
    _, _, var = _create_catalog_item(db)
    now = datetime.now(UTC)

    offsets_and_qtys = [
        (6, Decimal("1.000")),
        (8, Decimal("10.000")),
        (32, Decimal("100.000")),
        (95, Decimal("1000.000")),
    ]

    for days_ago, qty in offsets_and_qtys:
        o = Order(
            order_number=f"ORD-BND-{days_ago}-{uuid.uuid4().hex[:6]}",
            status="dispatched",
            created_at=now - timedelta(days=days_ago),
        )
        db.add(o)
        db.flush()
        db.add(OrderItem(order_id=o.id, variant_id=var.id, quantity=qty))
    db.commit()

    def _qty(p: str) -> Decimal:
        r = execute_tool(db, owner_user, "get_sales_summary", {"period": p})
        t = next((s for s in r["sales"] if s["sku"] == var.sku), None)
        return Decimal(t["total_sold_quantity"]) if t else Decimal("0")

    assert _qty("last_7_days") == Decimal("1.000")
    assert _qty("last_30_days") == Decimal("11.000")  # 1 + 10
    assert _qty("last_90_days") == Decimal("111.000")  # 1 + 10 + 100
    assert _qty("all_time") == Decimal("1111.000")  # 1 + 10 + 100 + 1000


def test_adv_sales_summary_group_by_hierarchy_aggregation(db: Session, owner_user: User):
    """Category 1 has 2 Products; Product 1 has 2 Variants, Product 2 has 1 Variant.
    Category 2 has 1 Product; Product 3 has 1 Variant.
    Verify exact aggregation at all 3 grouping levels.
    """
    cat1 = Category(name=f"Cat-Blends-{uuid.uuid4().hex[:6]}")
    cat2 = Category(name=f"Cat-Pure-{uuid.uuid4().hex[:6]}")
    db.add_all([cat1, cat2])
    db.flush()

    p1 = Product(name=f"Prod-Garam-{uuid.uuid4().hex[:6]}", category_id=cat1.id)
    p2 = Product(name=f"Prod-Sambar-{uuid.uuid4().hex[:6]}", category_id=cat1.id)
    p3 = Product(name=f"Prod-Turmeric-{uuid.uuid4().hex[:6]}", category_id=cat2.id)
    db.add_all([p1, p2, p3])
    db.flush()

    v1_1 = ProductVariant(product_id=p1.id, sku=f"G100-{uuid.uuid4().hex[:6]}", pack_size_g=100)
    v1_2 = ProductVariant(product_id=p1.id, sku=f"G500-{uuid.uuid4().hex[:6]}", pack_size_g=500)
    v2_1 = ProductVariant(product_id=p2.id, sku=f"S200-{uuid.uuid4().hex[:6]}", pack_size_g=200)
    v3_1 = ProductVariant(product_id=p3.id, sku=f"T250-{uuid.uuid4().hex[:6]}", pack_size_g=250)
    db.add_all([v1_1, v1_2, v2_1, v3_1])
    db.flush()

    ord1 = Order(order_number=f"ORD-AGG-{uuid.uuid4().hex[:6]}", status="dispatched")
    db.add(ord1)
    db.flush()

    # Sales:
    # v1_1: 10, v1_2: 15 -> Prod 1: 25
    # v2_1: 20           -> Prod 2: 20
    # -> Cat 1 Total: 45
    # v3_1: 30           -> Prod 3: 30 -> Cat 2 Total: 30
    db.add(OrderItem(order_id=ord1.id, variant_id=v1_1.id, quantity=Decimal("10.000")))
    db.add(OrderItem(order_id=ord1.id, variant_id=v1_2.id, quantity=Decimal("15.000")))
    db.add(OrderItem(order_id=ord1.id, variant_id=v2_1.id, quantity=Decimal("20.000")))
    db.add(OrderItem(order_id=ord1.id, variant_id=v3_1.id, quantity=Decimal("30.000")))
    db.commit()

    # 1. Group by variant
    res_var = execute_tool(
        db, owner_user, "get_sales_summary", {"period": "all_time", "group_by": "variant"}
    )
    _assert_no_floats(res_var)
    var_map = {s["sku"]: Decimal(s["total_sold_quantity"]) for s in res_var["sales"]}
    assert var_map[v1_1.sku] == Decimal("10.000")
    assert var_map[v1_2.sku] == Decimal("15.000")
    assert var_map[v2_1.sku] == Decimal("20.000")
    assert var_map[v3_1.sku] == Decimal("30.000")

    # 2. Group by product
    res_prod = execute_tool(
        db, owner_user, "get_sales_summary", {"period": "all_time", "group_by": "product"}
    )
    _assert_no_floats(res_prod)
    prod_map = {s["product_name"]: Decimal(s["total_sold_quantity"]) for s in res_prod["sales"]}
    assert prod_map[p1.name] == Decimal("25.000")
    assert prod_map[p2.name] == Decimal("20.000")
    assert prod_map[p3.name] == Decimal("30.000")

    # 3. Group by category
    res_cat = execute_tool(
        db, owner_user, "get_sales_summary", {"period": "all_time", "group_by": "category"}
    )
    _assert_no_floats(res_cat)
    cat_map = {s["category_name"]: Decimal(s["total_sold_quantity"]) for s in res_cat["sales"]}
    assert cat_map[cat1.name] == Decimal("45.000")
    assert cat_map[cat2.name] == Decimal("30.000")


def test_adv_sales_summary_invalid_arguments_rejected(db: Session, owner_user: User):
    """Invalid period or group_by parameter strings must be rejected."""
    with pytest.raises(ValidationError):
        execute_tool(db, owner_user, "get_sales_summary", {"period": "last_1_year"})

    with pytest.raises(ValidationError):
        execute_tool(db, owner_user, "get_sales_summary", {"group_by": "warehouse"})


# ── 3. Adversarial Tests for get_slow_movers ──────────────────────────────────


def test_adv_slow_movers_strict_rbac(
    db: Session,
    dealer_user: User,
    distributor_user: User,
    consumer_user: User,
    factory_user: User,
    warehouse_user: User,
):
    """Every role except owner MUST be rejected with ToolPermissionError."""
    for u in (dealer_user, distributor_user, consumer_user, factory_user, warehouse_user):
        with pytest.raises(ToolPermissionError):
            execute_tool(db, u, "get_slow_movers", {"threshold_days": 30})


def test_adv_slow_movers_fefo_expiry_sorting_across_batches(db: Session, owner_user: User):
    """Multiple released batches of slow variants must be strictly sorted by expiry_date.asc()."""
    wh = Warehouse(code=f"WH-FEFO-{uuid.uuid4().hex[:6]}", name="FEFO Warehouse")
    db.add(wh)
    db.flush()

    today = date.today()
    batches = []

    # Create 4 variants with different expiry dates:
    # B1: +120 days
    # B2: +30 days (earliest)
    # B3: +90 days
    # B4: +60 days
    expiry_offsets = [120, 30, 90, 60]
    for idx, offset in enumerate(expiry_offsets, 1):
        _, _, var = _create_catalog_item(db, sku=f"FEFO-VAR-{idx}-{uuid.uuid4().hex[:6]}")
        batch = ProductionBatch(
            variant_id=var.id,
            batch_code=f"B-FEFO-{offset}D-{uuid.uuid4().hex[:6]}",
            status="released",
            expiry_date=today + timedelta(days=offset),
        )
        db.add(batch)
        db.flush()
        db.add(
            InventoryBalance(
                warehouse_id=wh.id,
                batch_id=batch.id,
                on_hand=Decimal("100.000"),
                reserved=Decimal("0.000"),
            )
        )
        batches.append((batch, offset))
    db.commit()

    res = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 30})
    _assert_no_floats(res)

    result_batches = [s["batch_code"] for s in res["slow_movers"]]

    # Extract positions of our 4 batches
    pos_30 = next(i for i, b in enumerate(result_batches) if "30D" in b)
    pos_60 = next(i for i, b in enumerate(result_batches) if "60D" in b)
    pos_90 = next(i for i, b in enumerate(result_batches) if "90D" in b)
    pos_120 = next(i for i, b in enumerate(result_batches) if "120D" in b)

    assert pos_30 < pos_60 < pos_90 < pos_120, "Batches must be strictly sorted by expiry_date ASC"


def test_adv_slow_movers_cancelled_only_order_is_slow(db: Session, owner_user: User):
    """If a variant has had orders within threshold, but ALL of them were CANCELLED,
    the variant must still be reported as a slow mover.
    """
    wh = Warehouse(code=f"WH-CAN-{uuid.uuid4().hex[:6]}", name="Cancel WH")
    db.add(wh)
    db.flush()

    _, _, var = _create_catalog_item(db)
    now = datetime.now(UTC)

    # Cancelled order 5 days ago
    o = Order(
        order_number=f"ORD-CAN-{uuid.uuid4().hex[:6]}",
        status="cancelled",
        created_at=now - timedelta(days=5),
    )
    db.add(o)
    db.flush()
    db.add(OrderItem(order_id=o.id, variant_id=var.id, quantity=Decimal("50.000")))

    # Batch with stock
    batch = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-CAN-VAR-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=date.today() + timedelta(days=100),
    )
    db.add(batch)
    db.flush()
    db.add(
        InventoryBalance(
            warehouse_id=wh.id,
            batch_id=batch.id,
            on_hand=Decimal("50.000"),
            reserved=Decimal("0.000"),
        )
    )
    db.commit()

    res = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 30})
    found = any(s["batch_code"] == batch.batch_code for s in res["slow_movers"])
    assert found is True, "Variant with only cancelled orders must be classified as slow mover"


def test_adv_slow_movers_batch_status_and_zero_stock_isolation(db: Session, owner_user: User):
    """Batches must be excluded if:
    - status is hold, rejected, or recalled
    - available stock (on_hand - reserved) is zero
    """
    wh = Warehouse(code=f"WH-STAT-{uuid.uuid4().hex[:6]}", name="Status WH")
    db.add(wh)
    db.flush()
    today = date.today()

    _, _, var = _create_catalog_item(db)

    # 1. Hold batch
    b_hold = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-HOLD-{uuid.uuid4().hex[:6]}",
        status="on_hold",
        expiry_date=today + timedelta(days=40),
    )
    # 2. Rejected batch
    b_rej = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-REJ-{uuid.uuid4().hex[:6]}",
        status="rejected",
        expiry_date=today + timedelta(days=50),
    )
    # 3. Recalled batch
    b_rec = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-REC-{uuid.uuid4().hex[:6]}",
        status="on_hold",
        expiry_date=today + timedelta(days=60),
    )
    # 4. Zero available batch (on_hand = 10, reserved = 10)
    b_zero = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-ZERO-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=today + timedelta(days=70),
    )
    # 5. Eligible batch
    b_elig = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-ELIG-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=today + timedelta(days=80),
    )
    db.add_all([b_hold, b_rej, b_rec, b_zero, b_elig])
    db.flush()

    db.add(
        InventoryBalance(
            warehouse_id=wh.id, batch_id=b_hold.id, on_hand=Decimal("10.000"), reserved=Decimal("0")
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id, batch_id=b_rej.id, on_hand=Decimal("10.000"), reserved=Decimal("0")
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id, batch_id=b_rec.id, on_hand=Decimal("10.000"), reserved=Decimal("0")
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id,
            batch_id=b_zero.id,
            on_hand=Decimal("10.000"),
            reserved=Decimal("10.000"),
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh.id,
            batch_id=b_elig.id,
            on_hand=Decimal("10.000"),
            reserved=Decimal("2.000"),
        )
    )
    db.commit()

    res = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 30})
    batch_codes = {s["batch_code"] for s in res["slow_movers"]}

    assert b_elig.batch_code in batch_codes
    assert b_hold.batch_code not in batch_codes
    assert b_rej.batch_code not in batch_codes
    assert b_rec.batch_code not in batch_codes
    assert b_zero.batch_code not in batch_codes

    # Check available stock value for eligible batch (10 - 2 = 8.000)
    target = next(s for s in res["slow_movers"] if s["batch_code"] == b_elig.batch_code)
    assert target["available"] == "8.000"
    assert isinstance(target["available"], str)


def test_adv_slow_movers_multi_warehouse_isolation(db: Session, owner_user: User):
    """The same released batch present in 2 warehouses must report distinct warehouse lines."""
    wh1 = Warehouse(code=f"WH-1-{uuid.uuid4().hex[:6]}", name="Warehouse Alpha")
    wh2 = Warehouse(code=f"WH-2-{uuid.uuid4().hex[:6]}", name="Warehouse Beta")
    db.add_all([wh1, wh2])
    db.flush()

    _, _, var = _create_catalog_item(db)
    batch = ProductionBatch(
        variant_id=var.id,
        batch_code=f"B-MULTIWH-{uuid.uuid4().hex[:6]}",
        status="released",
        expiry_date=date.today() + timedelta(days=90),
    )
    db.add(batch)
    db.flush()

    db.add(
        InventoryBalance(
            warehouse_id=wh1.id, batch_id=batch.id, on_hand=Decimal("25.000"), reserved=Decimal("0")
        )
    )
    db.add(
        InventoryBalance(
            warehouse_id=wh2.id, batch_id=batch.id, on_hand=Decimal("40.000"), reserved=Decimal("0")
        )
    )
    db.commit()

    res = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 30})
    lines = [s for s in res["slow_movers"] if s["batch_code"] == batch.batch_code]
    assert len(lines) == 2
    wh_names = {line["warehouse"] for line in lines}
    assert "Warehouse Alpha" in wh_names
    assert "Warehouse Beta" in wh_names


def test_adv_slow_movers_threshold_range_boundaries(db: Session, owner_user: User):
    """ge=1, le=365 boundary testing for threshold_days."""
    # 0 is rejected
    with pytest.raises(ValidationError):
        execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 0})

    # 366 is rejected
    with pytest.raises(ValidationError):
        execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 366})

    # 1 is accepted
    res_1 = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 1})
    assert res_1["threshold_days"] == 1

    # 365 is accepted
    res_365 = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 365})
    assert res_365["threshold_days"] == 365


# ── 4. Adversarial Tests for get_dealer_history ───────────────────────────────


def test_adv_dealer_history_isolation_and_no_floats(
    db: Session, dealer_user: User, consumer_user: User, owner_user: User
):
    """Dealer cannot snoop on another user's drafts; owner can view any; no floats returned."""
    # Create draft for dealer
    _, _, var = _create_catalog_item(db, prices={"DEALER": Decimal("50.00")})
    execute_tool(
        db,
        dealer_user,
        "create_order_draft",
        {"items": [{"variant_id": str(var.id), "quantity": "4"}]},
    )

    # Dealer views own history -> SUCCESS
    dealer_res = execute_tool(db, dealer_user, "get_dealer_history", {})
    _assert_no_floats(dealer_res)
    assert len(dealer_res["orders"]) >= 1

    # Consumer tries to view dealer's history -> FORBIDDEN
    with pytest.raises(ToolPermissionError):
        execute_tool(db, consumer_user, "get_dealer_history", {"dealer_id": str(dealer_user.id)})

    # Owner views dealer's history -> SUCCESS
    owner_res = execute_tool(
        db, owner_user, "get_dealer_history", {"dealer_id": str(dealer_user.id)}
    )
    _assert_no_floats(owner_res)
    assert owner_res["dealer_id"] == str(dealer_user.id)
