"""Empirical Challenger 2 verification suite.

Stress-tests:
1. AST & SQL dialect compilation for row locking (`with_for_update()`).
2. Draft approval concurrency, sequential double/triple approvals, DB order count invariant.
3. Cross-user authorization matrix (RBAC) on draft approval.
4. Sliding-window rate limiting on `/api/v1/ai/chat` (20 req/60s burst, 21st -> 429).
5. Sliding-window rate limiting on `/api/v1/ai/agent/query` (30 req/60s burst, 31st -> 429).
6. Reset behavior immediately restoring throughput.
7. IP-based client isolation and endpoint-to-endpoint bucket isolation.
8. Test fixture isolation and state leakage across repeated execution cycles.
"""

import inspect
import threading
import time
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.ai.models import OrderDraft
from app.ai.router import ai_agent_rate_limiter, ai_chat_rate_limiter, approve_order_draft
from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, Product, ProductVariant
from app.core.errors import AppError
from app.inventory.models import Order, OrderItem
from tests.conftest import _make_user


def _create_test_draft(
    db: Session,
    customer: User,
    status: str = "draft",
    num_items: int = 1,
) -> tuple[OrderDraft, list[ProductVariant]]:
    """Helper to create a valid draft with catalog items."""
    cat = Category(name=f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"Prod-{uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    variants = []
    items_data = []
    total = Decimal("0.00")
    for i in range(num_items):
        var = ProductVariant(
            product_id=prod.id,
            sku=f"SKU-{uuid.uuid4().hex[:6]}-{i}",
            pack_size_g=250,
        )
        db.add(var)
        db.flush()
        variants.append(var)
        unit_price = Decimal("150.00")
        qty = Decimal("5.000")
        line_total = unit_price * qty
        total += line_total
        items_data.append(
            {
                "variant_id": str(var.id),
                "sku": var.sku,
                "quantity": str(qty),
                "unit_price": str(unit_price),
                "line_total": str(line_total),
            }
        )

    draft = OrderDraft(
        customer_id=customer.id,
        items=items_data,
        total_estimated=total,
        status=status,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft, variants


def _get_auth_header(
    client: TestClient, user: User, password: str = "testpass123"
) -> dict[str, str]:
    """Generate JWT auth header directly without consuming login rate limiter quota."""
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ── 1. AST & SQL Dialect Inspection ──────────────────────────────────────────


def test_ast_source_code_locking_inspection():
    """Verify approve_order_draft explicitly includes with_for_update row-lock call."""
    source = inspect.getsource(approve_order_draft)
    assert ".with_for_update()" in source, "approve_order_draft MUST call .with_for_update()"
    assert "select(OrderDraft)" in source, "Query must select OrderDraft entity"
    assert "DRAFT_ALREADY_PROCESSED" in source, "Must raise DRAFT_ALREADY_PROCESSED when not draft"
    assert 'draft.status = "approved"' in source, "Must transition status to approved"


def test_sql_compilation_with_for_update_dialects():
    """Verify that SQLAlchemy compiles FOR UPDATE clause for PostgreSQL dialect."""
    query = select(OrderDraft).where(OrderDraft.id == uuid.uuid4()).with_for_update()

    pg_sql = str(query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in pg_sql, "PostgreSQL compiled SQL must include 'FOR UPDATE'"

    sqlite_sql = str(query.compile(dialect=sqlite.dialect()))
    assert "SELECT" in sqlite_sql, "SQLite compiles without syntax error"


# ── 2. Draft Approval Concurrency & Invariants ──────────────────────────────


def test_sequential_quadruple_approval_rejection(client: TestClient, db: Session):
    """Stress test: 1 success followed by 3 rejected approvals; DB has exactly 1 order."""
    dealer = _make_user(
        db, email=f"dealer-quad-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer"
    )
    draft, variants = _create_test_draft(db, dealer, status="draft", num_items=2)
    headers = _get_auth_header(client, dealer)

    # 1st attempt: Must succeed
    resp1 = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
    assert resp1.status_code == 200
    res1_data = resp1.json()
    assert res1_data["status"] == "approved"
    assert res1_data["order_id"] is not None
    created_order_id = res1_data["order_id"]

    # 2nd, 3rd, 4th attempts: All must fail with 400 DRAFT_ALREADY_PROCESSED
    for attempt in range(2, 5):
        resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
        assert resp.status_code == 400, f"Attempt {attempt} got {resp.status_code}"
        data = resp.json()
        assert data["code"] == "DRAFT_ALREADY_PROCESSED", f"Attempt {attempt} bad code: {data}"
        assert "already 'approved'" in data["message"]

    # Invariant: Verify DB contains exactly 1 order and 2 order items matching draft
    orders = db.scalars(select(Order)).all()
    assert len(orders) == 1, f"Expected 1 order, found {len(orders)}"
    assert str(orders[0].id) == created_order_id

    order_items = db.scalars(select(OrderItem).where(OrderItem.order_id == orders[0].id)).all()
    assert len(order_items) == 2, f"Expected 2 order items, found {len(order_items)}"
    variant_ids_in_order = {str(item.variant_id) for item in order_items}
    expected_variant_ids = {str(v.id) for v in variants}
    assert variant_ids_in_order == expected_variant_ids


def test_approve_draft_terminal_statuses(client: TestClient, db: Session):
    """Drafts in 'rejected' status cannot be approved and raise DRAFT_ALREADY_PROCESSED."""
    dealer = _make_user(
        db, email=f"dealer-term-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer"
    )
    headers = _get_auth_header(client, dealer)

    draft, _ = _create_test_draft(db, dealer, status="rejected")
    resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "DRAFT_ALREADY_PROCESSED"
    assert "already 'rejected'" in data["message"]

    # Invariant: No orders should have been created
    orders = db.scalars(select(Order)).all()
    assert len(orders) == 0


def test_approve_nonexistent_and_invalid_uuid(client: TestClient, db: Session):
    """Non-existent draft returns 404, invalid UUID string returns 422."""
    dealer = _make_user(
        db, email=f"dealer-404-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer"
    )
    headers = _get_auth_header(client, dealer)

    # 404 on valid UUID not in DB
    resp_404 = client.post(f"/api/v1/ai/drafts/{uuid.uuid4()}/approve", headers=headers)
    assert resp_404.status_code == 404
    assert resp_404.json()["code"] == "DRAFT_NOT_FOUND"

    # 422 on malformed UUID
    resp_422 = client.post("/api/v1/ai/drafts/not-a-valid-uuid/approve", headers=headers)
    assert resp_422.status_code == 422


# ── 3. Cross-User Authorization Matrix (RBAC) ───────────────────────────────


def test_cross_user_approval_security_matrix(client: TestClient, db: Session):
    """Exhaustively verify RBAC matrix for draft approval.

    - Target draft belongs to Dealer 1.
    - Dealer 2 (another dealer) -> 403 FORBIDDEN.
    - Consumer user -> 403 FORBIDDEN.
    - Factory user -> 403 FORBIDDEN.
    - Warehouse user -> 403 FORBIDDEN.
    - Distributor user -> 403 FORBIDDEN.
    - Unauthenticated request -> 401 Unauthorized.
    - Owner -> 200 OK (Owner is allowed to approve on behalf of dealer).
    """
    dealer1 = _make_user(db, email=f"d1-m-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    dealer2 = _make_user(db, email=f"d2-m-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    consumer = _make_user(
        db, email=f"cons-m-{uuid.uuid4().hex[:6]}@example.com", role_name="consumer"
    )
    factory = _make_user(
        db, email=f"fact-m-{uuid.uuid4().hex[:6]}@example.com", role_name="factory"
    )
    warehouse = _make_user(
        db, email=f"ware-m-{uuid.uuid4().hex[:6]}@example.com", role_name="warehouse"
    )
    distributor = _make_user(
        db, email=f"dist-m-{uuid.uuid4().hex[:6]}@example.com", role_name="distributor"
    )
    owner = _make_user(db, email=f"own-m-{uuid.uuid4().hex[:6]}@example.com", role_name="owner")

    draft, _ = _create_test_draft(db, dealer1, status="draft")

    # 1. Unauthenticated request (no header)
    resp_anon = client.post(f"/api/v1/ai/drafts/{draft.id}/approve")
    assert resp_anon.status_code in (401, 403)

    # 2. Unauthorized roles attempt approval
    unauthorized_users = [dealer2, consumer, factory, warehouse, distributor]
    for unauth_user in unauthorized_users:
        unauth_headers = _get_auth_header(client, unauth_user)
        resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=unauth_headers)
        assert (
            resp.status_code == 403
        ), f"Expected 403 for {unauth_user.email}, got {resp.status_code}"
        assert resp.json()["code"] == "FORBIDDEN"

    # Verify draft remains unmodified
    db.refresh(draft)
    assert draft.status == "draft"

    # 3. Privileged Owner attempts approval -> succeeds
    owner_headers = _get_auth_header(client, owner)
    resp_owner = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=owner_headers)
    assert resp_owner.status_code == 200
    assert resp_owner.json()["status"] == "approved"

    db.refresh(draft)
    assert draft.status == "approved"


def test_consumer_can_approve_own_draft(client: TestClient, db: Session):
    """Consumer user is allowed to approve their own order draft."""
    consumer = _make_user(
        db, email=f"cons-own-{uuid.uuid4().hex[:6]}@example.com", role_name="consumer"
    )
    draft, _ = _create_test_draft(db, consumer, status="draft")
    headers = _get_auth_header(client, consumer)

    resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


# ── 4. Rate Limiting Burst & Reset Verification ─────────────────────────────


def test_ai_chat_burst_20_and_21st_blocked_with_details(client: TestClient):
    """Burst test POST /api/v1/ai/chat: 20 succeed, 21st receives 429 with retry_after."""
    ai_chat_rate_limiter.reset()

    # Requests 1 to 20 MUST all succeed
    for i in range(20):
        resp = client.post("/api/v1/ai/chat", json={"query": f"Query {i}: spice origins"})
        assert resp.status_code == 200, f"Chat request {i+1} failed: {resp.status_code}"

    # Request 21 MUST be blocked with HTTP 429
    blocked = client.post("/api/v1/ai/chat", json={"query": "Query 21: blocked"})
    assert blocked.status_code == 429
    err = blocked.json()
    assert err["code"] == "RATE_LIMIT_EXCEEDED"
    assert "retry_after_seconds" in err.get("details", {})
    assert err["details"]["limit"] == 20
    assert err["details"]["retry_after_seconds"] >= 1

    # Immediate reset unblocks
    ai_chat_rate_limiter.reset()
    unblocked = client.post("/api/v1/ai/chat", json={"query": "Query after reset: should work"})
    assert unblocked.status_code == 200


def test_ai_agent_query_burst_30_and_31st_blocked_with_details(client: TestClient, db: Session):
    """Burst test POST /api/v1/ai/agent/query: 30 succeed, 31st receives 429 with retry_after."""
    ai_agent_rate_limiter.reset()
    dealer = _make_user(
        db, email=f"dealer-rate-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer"
    )
    headers = _get_auth_header(client, dealer)

    # Requests 1 to 30 MUST all succeed
    for i in range(30):
        resp = client.post(
            "/api/v1/ai/agent/query",
            json={"prompt": f"Query {i}: check stock of pepper"},
            headers=headers,
        )
        assert resp.status_code == 200, f"Agent request {i+1} failed: {resp.status_code}"

    # Request 31 MUST be blocked with HTTP 429
    blocked = client.post(
        "/api/v1/ai/agent/query",
        json={"prompt": "Query 31: blocked"},
        headers=headers,
    )
    assert blocked.status_code == 429
    err = blocked.json()
    assert err["code"] == "RATE_LIMIT_EXCEEDED"
    assert "retry_after_seconds" in err.get("details", {})
    assert err["details"]["limit"] == 30
    assert err["details"]["retry_after_seconds"] >= 1

    # Immediate reset unblocks
    ai_agent_rate_limiter.reset()
    unblocked = client.post(
        "/api/v1/ai/agent/query",
        json={"prompt": "Query after reset: should work"},
        headers=headers,
    )
    assert unblocked.status_code == 200


def test_rate_limiter_endpoint_bucket_isolation(client: TestClient, db: Session):
    """Exhausting ai_chat rate limiter does NOT exhaust ai_agent_query rate limiter."""
    ai_chat_rate_limiter.reset()
    ai_agent_rate_limiter.reset()

    dealer = _make_user(
        db, email=f"dealer-iso-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer"
    )
    headers = _get_auth_header(client, dealer)

    # Exhaust chat limiter (20 requests)
    for i in range(20):
        res = client.post("/api/v1/ai/chat", json={"query": f"Chat {i}"})
        assert res.status_code == 200
    assert client.post("/api/v1/ai/chat", json={"query": "Chat 21"}).status_code == 429

    # Agent query limiter MUST still be active and accept requests
    agent_res = client.post(
        "/api/v1/ai/agent/query",
        json={"prompt": "Agent query during chat block"},
        headers=headers,
    )
    assert agent_res.status_code == 200, "Agent query should NOT be affected by chat rate limit"


def test_rate_limiter_sliding_window_expiration():
    """Verify sliding-window logic expires timestamps older than window_seconds."""
    from app.core.rate_limit import RateLimiter

    limiter = RateLimiter(max_requests=2, window_seconds=10, name="test_exp")
    ip = "192.168.1.50"

    # Inject two timestamps: one 15 seconds ago (expired), one 5 seconds ago (active)
    now = time.time()
    limiter._history[ip] = [now - 15.0, now - 5.0]

    # Mock a request with client host
    class DummyClient:
        host = ip

    class DummyRequest:
        client = DummyClient()
        headers = {}

    req = DummyRequest()
    # Call limiter: should prune the 15-second-old timestamp, allowing this call
    limiter(req)
    assert len(limiter._history[ip]) == 2

    # Next call should exceed max_requests (2 active within 10s) and raise AppError 429
    with pytest.raises(AppError) as exc_info:
        limiter(req)
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"


# ── 5. Test Fixture Isolation & Leakage Stress ──────────────────────────────


@pytest.mark.parametrize("cycle", range(1, 4))
def test_repeated_cycles_rate_limit_isolation(cycle: int, client: TestClient):
    """Parametrized test running 3 repeated cycles.

    Verifies autouse fixture _setup_db properly resets rate limiters between tests.
    Each test iteration MUST start with 0 used requests and successfully send 20 requests.
    """
    for i in range(20):
        resp = client.post("/api/v1/ai/chat", json={"query": f"Cycle {cycle} Req {i}"})
        assert resp.status_code == 200, f"Cycle {cycle} request {i+1} failed"

    # 21st request must be 429
    blocked = client.post("/api/v1/ai/chat", json={"query": f"Cycle {cycle} Req 21"})
    assert blocked.status_code == 429


# ── 6. Advanced Concurrency & Adversarial Stress Tests ───────────────────────


def test_rate_limiter_multithreaded_concurrency_stress():
    """50 concurrent threads hit RateLimiter(max_requests=20) at the exact same instant.

    Enforces thread-safety invariant:
    - Exactly 20 threads succeed.
    - Exactly 30 threads fail with AppError 429 RATE_LIMIT_EXCEEDED.
    - Exactly 20 recorded timestamps in internal history.
    """
    from app.core.rate_limit import RateLimiter

    limiter = RateLimiter(max_requests=20, window_seconds=60, name="thread_stress")
    client_ip = "192.168.100.99"

    class DummyClient:
        host = client_ip

    class DummyRequest:
        client = DummyClient()
        headers = {}

    req = DummyRequest()
    num_threads = 50
    barrier = threading.Barrier(num_threads)
    success_count = 0
    failure_count = 0
    lock = threading.Lock()

    def _worker():
        nonlocal success_count, failure_count
        barrier.wait()
        try:
            limiter(req)
            with lock:
                success_count += 1
        except AppError as e:
            if e.status_code == 429 and e.code == "RATE_LIMIT_EXCEEDED":
                with lock:
                    failure_count += 1
            else:
                raise

    threads = [threading.Thread(target=_worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert success_count == 20, f"Expected 20 successes, got {success_count}"
    assert failure_count == 30, f"Expected 30 failures, got {failure_count}"
    assert len(limiter._history[client_ip]) == 20


def test_rate_limiter_multi_ip_isolation():
    """Multiple distinct IPs maintain independent sliding-window quotas."""
    from app.core.rate_limit import RateLimiter

    limiter = RateLimiter(max_requests=5, window_seconds=60, name="multi_ip")

    for ip_suffix in range(1, 4):
        ip = f"10.0.0.{ip_suffix}"

        class DummyClient:
            host = ip

        class DummyRequest:
            client = DummyClient()
            headers = {}

        req = DummyRequest()
        # Each IP sends 5 requests without interference
        for _ in range(5):
            limiter(req)

        # 6th request for this IP must fail
        with pytest.raises(AppError) as exc_info:
            limiter(req)
        assert exc_info.value.status_code == 429


def test_chat_endpoint_adversarial_queries(client: TestClient):
    """Stress test /api/v1/ai/chat with edge-case payloads: boundaries, giant, unicode."""
    ai_chat_rate_limiter.reset()

    # 1. Below min_length (1 char or empty) -> 422 Unprocessable Entity
    resp_empty = client.post("/api/v1/ai/chat", json={"query": ""})
    assert resp_empty.status_code == 422

    resp_single = client.post("/api/v1/ai/chat", json={"query": "a"})
    assert resp_single.status_code == 422

    # 2. Exceeding max_length (501 chars) -> 422 Unprocessable Entity
    giant_text = "What are the specs of turmeric? " * 20  # > 500 chars
    assert len(giant_text) > 500
    resp_giant = client.post("/api/v1/ai/chat", json={"query": giant_text})
    assert resp_giant.status_code == 422

    # 3. Valid Unicode and non-ASCII Hindi query within limits -> 200 OK
    resp_unicode = client.post(
        "/api/v1/ai/chat",
        json={"query": "हल्दी और काली मिर्च का स्टॉक कितना है? 🌶️ 📦"},
    )
    assert resp_unicode.status_code == 200

    # 4. Invariant: 422 requests also consume rate limiter quota because FastAPI
    # executes router dependencies before body parsing
    # We already sent 4 requests above (empty, single, giant, unicode), so 16 more should exhaust 20
    for i in range(16):
        res = client.post("/api/v1/ai/chat", json={"query": f"Valid query {i}"})
        assert res.status_code == 200

    # The 21st request must receive HTTP 429
    blocked = client.post("/api/v1/ai/chat", json={"query": "Query 21"})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMIT_EXCEEDED"
