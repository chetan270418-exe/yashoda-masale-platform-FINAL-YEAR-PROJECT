"""Auth module tests (T-021, T-022).

Covers:
- Consumer registration (B10)
- Login returns a JWT
- /auth/me with valid / missing token
- Staff creation by owner / non-owner
- Duplicate email rejection
- Public sign-up cannot choose a non-consumer role (B10)
"""

from app.audit.models import AuditLog
from app.auth.security import create_access_token
from tests.conftest import _make_user

# ── Consumer registration ─────────────────────────────────────────────────────


def test_register_consumer_creates_consumer(client):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "securepass1",
            "full_name": "Alice A",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert body["role_name"] == "consumer"
    assert body["is_active"] is True


def test_register_duplicate_email_fails(client):
    payload = {
        "email": "dup@example.com",
        "password": "securepass1",
        "full_name": "Dup User",
    }
    r1 = client.post("/api/v1/auth/register", json=payload)
    assert r1.status_code == 201

    r2 = client.post("/api/v1/auth/register", json=payload)
    assert r2.status_code == 409
    assert r2.json()["code"] == "DUPLICATE_EMAIL"


def test_register_short_password_rejected(client):
    r = client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "short", "full_name": "X"},
    )
    assert r.status_code == 422


def test_public_signup_cannot_choose_role(client):
    """B10: the register endpoint has no role_name field — it always creates a consumer.

    Even if someone somehow sends a role_name field, Pydantic ignores extra fields
    and the service forces the consumer role.
    """
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": "hacker@example.com",
            "password": "securepass1",
            "full_name": "Hacker",
            "role_name": "owner",  # extra field — must be ignored
        },
    )
    assert r.status_code == 201
    assert r.json()["role_name"] == "consumer"


# ── Login ─────────────────────────────────────────────────────────────────────


def test_login_returns_valid_jwt(client):
    # Register first
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "bob@example.com",
            "password": "securepass1",
            "full_name": "Bob B",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "securepass1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


def test_login_bad_password_returns_401(client):
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "carol@example.com",
            "password": "securepass1",
            "full_name": "Carol",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrongpass123"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "BAD_CREDENTIALS"


# ── /auth/me ──────────────────────────────────────────────────────────────────


def test_me_returns_user_info_with_valid_token(client):
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "dave@example.com",
            "password": "securepass1",
            "full_name": "Dave D",
        },
    )
    login_r = client.post(
        "/api/v1/auth/login",
        json={"email": "dave@example.com", "password": "securepass1"},
    )
    token = login_r.json()["access_token"]

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "dave@example.com"
    assert r.json()["role_name"] == "consumer"


def test_me_returns_401_without_token(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_me_returns_401_with_invalid_token(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert r.status_code == 401


# ── Staff creation ────────────────────────────────────────────────────────────


def test_staff_creation_by_owner_works(client, db):
    owner = _make_user(db, email="owner@test.com", password="ownerpass1", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    r = client.post(
        "/api/v1/auth/staff",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "factory@test.com",
            "password": "staffpass1",
            "full_name": "Factory Manager",
            "role_name": "factory",
        },
    )
    assert r.status_code == 201
    assert r.json()["role_name"] == "factory"

    # Verify audit log was written (rules.md section 5, FR-AUTH-05)
    audit = db.query(AuditLog).filter_by(action="CREATE_STAFF_USER").first()
    assert audit is not None
    assert audit.actor_id == owner.id
    assert audit.entity == "users"
    assert audit.after["role"] == "factory"


def test_staff_creation_by_non_owner_returns_403(client, db):
    dealer = _make_user(db, email="dealer@test.com", password="dealerpass1", role_name="dealer")
    token = create_access_token({"sub": str(dealer.id), "role": "dealer"})

    r = client.post(
        "/api/v1/auth/staff",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "sneaky@test.com",
            "password": "staffpass1",
            "full_name": "Sneaky",
            "role_name": "warehouse",
        },
    )
    assert r.status_code == 403


def test_staff_creation_with_consumer_role_rejected(client, db):
    """Owner cannot create a consumer via /staff — they must self-register (B10)."""
    owner = _make_user(db, email="owner2@test.com", password="ownerpass1", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    r = client.post(
        "/api/v1/auth/staff",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "newconsumer@test.com",
            "password": "consumerpass1",
            "full_name": "Consumer Via Staff",
            "role_name": "consumer",
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_ROLE"


# ── Users listing ─────────────────────────────────────────────────────────────


def test_list_users_owner_only(client, db):
    owner = _make_user(db, email="listowner@test.com", password="ownerpass1", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    r = client.get("/api/v1/auth/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_list_users_consumer_forbidden(client, db):
    consumer = _make_user(
        db, email="consumer_list@test.com", password="conspass1", role_name="consumer"
    )
    token = create_access_token({"sub": str(consumer.id), "role": "consumer"})

    r = client.get("/api/v1/auth/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# ── Deactivated Account & Rate Limit Tests ────────────────────────────────────


def test_login_deactivated_user_rejected(client, db):
    user = _make_user(db, email="disabled@test.com", password="securepass1", role_name="consumer")
    user.is_active = False
    db.commit()

    r = client.post(
        "/api/v1/auth/login",
        json={"email": "disabled@test.com", "password": "securepass1"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "ACCOUNT_DISABLED"


def test_me_deactivated_user_rejected(client, db):
    user = _make_user(db, email="was_active@test.com", password="securepass1", role_name="consumer")
    token = create_access_token({"sub": str(user.id), "role": "consumer"})

    # User is deactivated after token issue
    user.is_active = False
    db.commit()

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert r.json()["code"] == "ACCOUNT_DISABLED"


def test_login_rate_limiting_enforced(client):
    """5 failed or repeated login attempts allowed per window; 6th triggers 429."""
    for _ in range(5):
        r = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@test.com", "password": "badpassword"},
        )
        assert r.status_code in (401, 200)

    # 6th attempt must be rate-limited with HTTP 429
    r6 = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.com", "password": "badpassword"},
    )
    assert r6.status_code == 429
    body = r6.json()
    assert body["code"] == "RATE_LIMIT_EXCEEDED"
    assert "retry_after_seconds" in body["details"]


def test_login_rate_limiting_cannot_be_bypassed_with_spoofed_forwarded_for(client):
    """Item 4: X-Forwarded-For spoofing is ignored when trust_proxy_headers=False."""
    # Send 5 attempts with different spoofed X-Forwarded-For IPs
    for i in range(5):
        r = client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": f"198.51.100.{i}"},
            json={"email": "spoof@test.com", "password": "wrong"},
        )
        assert r.status_code == 401

    # 6th attempt with yet another spoofed IP must still be blocked
    r6 = client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "203.0.113.99"},
        json={"email": "spoof@test.com", "password": "wrong"},
    )
    assert r6.status_code == 429
    assert r6.json()["code"] == "RATE_LIMIT_EXCEEDED"


def test_owner_can_create_dealer_and_distributor_accounts(client, db):
    owner = _make_user(db, email="biz_owner@test.com", password="ownerpass1", role_name="owner")
    token = create_access_token({"sub": str(owner.id), "role": "owner"})

    # Create dealer
    r_dealer = client.post(
        "/api/v1/auth/staff",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "retailer@test.com",
            "password": "dealerpass1",
            "full_name": "Spice Retailer",
            "role_name": "dealer",
        },
    )
    assert r_dealer.status_code == 201
    assert r_dealer.json()["role_name"] == "dealer"

    # Create distributor
    r_dist = client.post(
        "/api/v1/auth/staff",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "wholesaler@test.com",
            "password": "distpass1",
            "full_name": "Regional Wholesaler",
            "role_name": "distributor",
        },
    )
    assert r_dist.status_code == 201
    assert r_dist.json()["role_name"] == "distributor"
