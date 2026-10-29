"""Concurrency and race-condition tests for AI order draft approval (Rule 62, Invariant B4/B8).

Verifies:
1. AST / inspect verification:
   - approve_order_draft uses select(OrderDraft)...with_for_update() to enforce row-level locking.
2. Sequential double-approval:
   - First request succeeds (creates exactly 1 order, sets status='approved').
   - Second request receives HTTP 400 with code='DRAFT_ALREADY_PROCESSED'.
   - Database has exactly 1 order (zero duplicate orders).
3. Multithreaded concurrent approval (PostgreSQL):
   - Two simultaneous threads attempt approval at the exact same instant using threading.Barrier.
   - Row-level lock serializes execution: exactly 1 thread succeeds,
     exactly 1 thread fails with DRAFT_ALREADY_PROCESSED.
   - Cleanly skips when running on SQLite (dialect check).
4. Edge cases & RBAC:
   - Cancelled or rejected drafts reject approval with HTTP 400.
   - Other users cannot approve someone else's draft (HTTP 403 FORBIDDEN).
   - Owner can approve on behalf of user.
   - Non-existent draft returns HTTP 404 DRAFT_NOT_FOUND.
"""

import inspect
import threading
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import OrderDraft
from app.ai.router import approve_order_draft
from app.auth.models import User
from app.catalog.models import Category, Product, ProductVariant
from app.core.errors import AppError
from app.inventory.models import Order
from tests.conftest import _engine, _make_user, _TestSession

# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_sample_draft(db: Session, customer: User, status: str = "draft") -> OrderDraft:
    """Create a Category, Product, Variant, and OrderDraft for testing."""
    cat = Category(name=f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(name=f"Product-{uuid.uuid4().hex[:6]}", category_id=cat.id)
    db.add(prod)
    db.flush()

    var = ProductVariant(product_id=prod.id, sku=f"SKU-{uuid.uuid4().hex[:6]}", pack_size_g=500)
    db.add(var)
    db.flush()

    draft = OrderDraft(
        customer_id=customer.id,
        items=[
            {
                "variant_id": str(var.id),
                "sku": var.sku,
                "quantity": "10",
                "unit_price": "120.00",
                "line_total": "1200.00",
            }
        ],
        total_estimated=Decimal("1200.00"),
        status=status,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


# ── 1. Static AST / Code Inspection ───────────────────────────────────────────


def test_approve_draft_locking_query_inspection():
    """Verify that approve_order_draft executes row-level locking via .with_for_update()."""
    source = inspect.getsource(approve_order_draft)
    assert ".with_for_update()" in source, "approve_order_draft MUST acquire row-level lock"
    assert "select(OrderDraft)" in source, "Query must select OrderDraft"
    assert "DRAFT_ALREADY_PROCESSED" in source, "Status check must raise DRAFT_ALREADY_PROCESSED"


# ── 2. Sequential Double-Approval Tests ────────────────────────────────────────


def test_sequential_double_approval_rejected(client: TestClient, db: Session):
    """Calling approve twice sequentially succeeds first, then returns HTTP 400."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    draft = _create_sample_draft(db, dealer, status="draft")

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": dealer.email, "password": "testpass123"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. First approval request succeeds
    resp1 = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "approved"
    assert data1["order_id"] is not None

    # 2. Second approval request receives HTTP 400 DRAFT_ALREADY_PROCESSED
    resp2 = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
    assert resp2.status_code == 400
    data2 = resp2.json()
    assert data2["code"] == "DRAFT_ALREADY_PROCESSED"
    assert "already 'approved'" in data2["message"]

    # 3. Assert exactly 1 order exists in database
    orders = db.scalars(select(Order)).all()
    assert len(orders) == 1
    assert str(orders[0].id) == data1["order_id"]


# ── 3. Multithreaded Concurrency on PostgreSQL ────────────────────────────────


def test_multithreaded_draft_approval_concurrency():
    """Two concurrent threads simultaneously attempt to approve the same draft.

    Enforces:
    - Exactly 1 thread succeeds (status='approved', creates Order).
    - Exactly 1 thread fails with DRAFT_ALREADY_PROCESSED.
    - Exactly 1 Order row exists in the database.
    - Zero duplicate orders.
    """
    if _engine.dialect.name != "postgresql":
        pytest.skip(
            "Multithreaded concurrency tests require PostgreSQL with row-level FOR UPDATE locking. "
            "Set TEST_DATABASE_URL to run."
        )

    setup_session = _TestSession()
    dealer = _make_user(
        setup_session,
        email=f"dealer-concur-{uuid.uuid4().hex[:6]}@example.com",
        role_name="dealer",
    )
    draft = _create_sample_draft(setup_session, dealer, status="draft")
    draft_id = draft.id
    dealer_id = dealer.id
    setup_session.close()

    barrier = threading.Barrier(2)
    successes = []
    failures = []
    lock = threading.Lock()

    def _worker():
        thread_session = _TestSession()
        try:
            thread_user = thread_session.scalar(select(User).where(User.id == dealer_id))
            assert thread_user is not None
            # Synchronize threads to hit the approval function at the exact same instant
            barrier.wait()
            res = approve_order_draft(
                draft_id=draft_id,
                current_user=thread_user,
                db=thread_session,
            )
            with lock:
                successes.append(res)
        except Exception as exc:
            with lock:
                failures.append(exc)
        finally:
            thread_session.close()

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Invariant assertion: Exactly 1 succeeds, exactly 1 fails with DRAFT_ALREADY_PROCESSED
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}"
    assert isinstance(failures[0], AppError)
    assert failures[0].code == "DRAFT_ALREADY_PROCESSED"

    # Database invariant assertion: Exactly 1 order created
    verify_session = _TestSession()
    orders = verify_session.scalars(select(Order)).all()
    assert len(orders) == 1
    verify_session.close()


# ── 4. Edge Cases & RBAC Tests ────────────────────────────────────────────────


def test_approve_already_cancelled_draft_rejected(client: TestClient, db: Session):
    """Drafts in 'rejected' or other non-draft status cannot be approved."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    draft = _create_sample_draft(db, dealer, status="rejected")

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": dealer.email, "password": "testpass123"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == "DRAFT_ALREADY_PROCESSED"
    assert "already 'rejected'" in resp.json()["message"]


def test_approve_draft_unauthorized_user_forbidden(client: TestClient, db: Session):
    """User cannot approve an order draft belonging to another user."""
    dealer1 = _make_user(db, email=f"d1-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    dealer2 = _make_user(db, email=f"d2-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    draft1 = _create_sample_draft(db, dealer1, status="draft")

    # Dealer 2 logs in
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": dealer2.email, "password": "testpass123"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Dealer 2 tries to approve Dealer 1's draft
    resp = client.post(f"/api/v1/ai/drafts/{draft1.id}/approve", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


def test_owner_can_approve_dealer_draft(client: TestClient, db: Session):
    """Owner role is permitted to approve drafts for other users."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    owner = _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@example.com", role_name="owner")
    draft = _create_sample_draft(db, dealer, status="draft")

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": owner.email, "password": "testpass123"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(f"/api/v1/ai/drafts/{draft.id}/approve", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_approve_nonexistent_draft_returns_404(client: TestClient, db: Session):
    """Non-existent draft UUID returns HTTP 404 DRAFT_NOT_FOUND."""
    dealer = _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": dealer.email, "password": "testpass123"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    random_id = uuid.uuid4()
    resp = client.post(f"/api/v1/ai/drafts/{random_id}/approve", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "DRAFT_NOT_FOUND"
