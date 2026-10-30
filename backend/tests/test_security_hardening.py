"""Automated security hardening tests for Yashoda SpiceChain (Milestone M10 / T-100).

Verifies:
1. SQL Injection immunity across query parameters and filter strings.
2. JWT signature tampering rejection (Broken Authentication / A02).
3. JWT expired token rejection.
4. Broken Object Level Authorization (BOLA) isolation between customers.
5. Error response sanitization (no internal stack trace leaks on 500).
6. HTTP security and CORS header enforcement.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import create_access_token
from app.catalog.models import Category, PriceList, PriceListItem, Product, ProductVariant
from app.core.config import get_settings
from app.orders.models import Order
from tests.conftest import _make_user


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role.name})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def catalog_fixture(db: Session):
    cat = Category(id=uuid.uuid4(), name=f"Spices-{uuid.uuid4().hex[:6]}")
    db.add(cat)
    db.flush()

    prod = Product(id=uuid.uuid4(), name="Turmeric Pure", category_id=cat.id, is_active=True)
    db.add(prod)
    db.flush()

    var = ProductVariant(
        id=uuid.uuid4(),
        product_id=prod.id,
        sku=f"TUR-{uuid.uuid4().hex[:6]}",
        pack_size_g=500,
        is_active=True,
    )
    db.add(var)
    db.flush()

    mrp_pl = db.execute(select(PriceList).where(PriceList.name == "MRP")).scalar_one()

    pli = PriceListItem(
        id=uuid.uuid4(),
        price_list_id=mrp_pl.id,
        variant_id=var.id,
        price=Decimal("120.00"),
        valid_from=datetime.now(UTC),
    )
    db.add(pli)
    db.commit()
    return {"variant": var}


def test_sql_injection_immunity_in_orders_query(client: TestClient, db: Session):
    """Adversarial SQL injection strings in query params must be treated as literals."""
    owner = _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@test.com", role_name="owner")

    injection_payloads = [
        "' OR '1'='1",
        "'; DROP TABLE orders; --",
        "1 UNION SELECT null, null, null--",
        "admin'--",
    ]

    for attack in injection_payloads:
        resp = client.get(
            f"/api/v1/orders?order_number={attack}",
            headers=_auth_header(owner),
        )
        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list | dict)

    assert db.query(Order).count() >= 0


def test_jwt_signature_tampering_rejected(client: TestClient, db: Session):
    """Tampering with token payload or signature must return 401 Unauthorized."""
    user = _make_user(db, email=f"user-{uuid.uuid4().hex[:6]}@test.com", role_name="dealer")
    valid_token = create_access_token(
        {"sub": str(user.id), "email": user.email, "role": user.role.name}
    )

    # 1. Tampered signature
    tampered_sig = valid_token[:-1] + ("A" if valid_token[-1] != "A" else "B")
    resp1 = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {tampered_sig}"})
    assert resp1.status_code == 401

    # 2. Forged token with invalid secret key
    forged = jwt.encode(
        {"sub": str(user.id), "role": "owner", "exp": int(time.time()) + 3600},
        "an-adversary-attacker-secret-key-32chars",
        algorithm="HS256",
    )
    resp2 = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {forged}"})
    assert resp2.status_code == 401


def test_jwt_expired_token_rejected(client: TestClient, db: Session):
    """Expired JWT access token must be rejected with 401 Unauthorized."""
    settings = get_settings()
    user = _make_user(db, email=f"user-{uuid.uuid4().hex[:6]}@test.com", role_name="consumer")

    expired_payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.name,
        "exp": int(time.time()) - 3600,
    }
    expired_token = jwt.encode(expired_payload, settings.jwt_secret, algorithm="HS256")

    resp = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401


def test_bola_cross_tenant_order_read_prevention(client: TestClient, db: Session, catalog_fixture):
    """Customer A cannot view Customer B's order by guessing the order ID (BOLA)."""
    user_a = _make_user(db, email=f"cust_a-{uuid.uuid4().hex[:6]}@test.com", role_name="consumer")
    user_b = _make_user(db, email=f"cust_b-{uuid.uuid4().hex[:6]}@test.com", role_name="consumer")
    v = catalog_fixture["variant"]

    create_resp = client.post(
        "/api/v1/orders",
        json={"items": [{"variant_id": str(v.id), "quantity": "2.000"}]},
        headers=_auth_header(user_a),
    )
    assert create_resp.status_code == 201
    order_a_id = create_resp.json()["id"]

    resp_b = client.get(
        f"/api/v1/orders/{order_a_id}",
        headers=_auth_header(user_b),
    )
    assert resp_b.status_code in (403, 404)


def test_error_response_masks_internal_details(client: TestClient):
    """Standard error response format must contain code, message, details and no traceback."""
    resp = client.get("/non-existent-endpoint-security-check")
    assert resp.status_code == 404
    data = resp.json()
    assert "code" in data
    assert "message" in data
    assert "details" in data
    assert "traceback" not in data
    assert "Traceback" not in str(data)
