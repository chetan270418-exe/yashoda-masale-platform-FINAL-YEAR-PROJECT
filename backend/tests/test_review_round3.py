# ruff: noqa: F811  (fixtures are imported from other test modules)
"""Regression tests for the round-3 review: release gate, invoices, QR scans, health, backups."""

import hashlib
import subprocess
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.auth.security import create_access_token
from app.core.config import Settings, get_settings
from app.core.rate_limit import get_client_ip
from app.traceability import service as trace_service
from scripts import backup_db
from tests import test_orders_fulfillment as orders_helpers
from tests.conftest import _make_user
from tests.test_orders_fulfillment import fulfillment_catalog, warehouse  # noqa: F401  (fixtures)
from tests.test_production_records import (  # noqa: F401  (pytest fixtures)
    _auth_header,
    batch,
    factory_user,
    plan,
    variant,
)


def _bearer(db, role: str) -> dict[str, str]:
    user = _make_user(db, email=f"{role}-{uuid.uuid4().hex[:6]}@t.com", role_name=role)
    token = create_access_token({"sub": str(user.id), "role": role})
    return {"Authorization": f"Bearer {token}"}


# ── 1. The quality gate cannot be skipped through /transition ────────────────


def test_transition_endpoint_cannot_release_a_batch(client, db, batch, factory_user):
    def go(to):
        return client.post(
            f"/api/v1/production/batches/{batch.id}/transition",
            json={"to_status": to},
            headers=_auth_header(factory_user),
        )

    assert go("in_production").status_code == 200
    assert go("quality_pending").status_code == 200
    r = go("released")  # no quality check was ever recorded
    assert r.status_code == 400
    assert r.json()["code"] == "USE_QUALITY_DECISION"
    db.expire_all()
    db.refresh(batch)
    assert batch.status == "quality_pending"


# ── 2. No invoice for, and no payment against, a cancelled order ─────────────


def test_cancelled_order_cannot_be_invoiced_or_paid(client, db, fulfillment_catalog, warehouse):
    consumer = _make_user(db, email="inv_c@test.com", role_name="consumer")
    staff = _make_user(db, email="inv_s@test.com", role_name="warehouse")
    var = fulfillment_catalog["var_a"]

    cancelled = orders_helpers._create_order_via_api(client, consumer, var.id, quantity="2.000")
    assert (
        client.post(
            f"/api/v1/orders/{cancelled['id']}/cancel", headers=orders_helpers._auth_header(staff)
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/v1/orders/{cancelled['id']}/invoices", headers=orders_helpers._auth_header(staff)
    )
    assert r.status_code == 400 and r.json()["code"] == "ORDER_CANCELLED"

    invoiced = orders_helpers._create_order_via_api(client, consumer, var.id, quantity="2.000")
    inv = client.post(
        f"/api/v1/orders/{invoiced['id']}/invoices", headers=orders_helpers._auth_header(staff)
    )
    assert inv.status_code == 201
    client.post(
        f"/api/v1/orders/{invoiced['id']}/cancel", headers=orders_helpers._auth_header(staff)
    )
    r = client.post(
        f"/api/v1/orders/invoices/{inv.json()['id']}/pay",
        json={"amount": inv.json()["total_amount"]},
        headers=orders_helpers._auth_header(consumer),
    )
    assert r.status_code == 400 and r.json()["code"] == "ORDER_CANCELLED"


# ── 3. Scan log: real total, keyed IP hash, proxy-aware client IP ────────────


def test_scan_log_total_is_the_full_count_not_the_page_size(client, db):
    for _ in range(3):
        assert client.get(f"/api/v1/traceability/{uuid.uuid4().hex}").status_code == 404
    r = client.get("/api/v1/traceability/scans?limit=2", headers=_bearer(db, "owner"))
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2
    assert r.json()["total"] == 3


def test_ip_hash_is_keyed_not_plain_sha256():
    plain = hashlib.sha256(b"203.0.113.9").hexdigest()
    assert trace_service.hash_ip("203.0.113.9") != plain
    assert trace_service.hash_ip("203.0.113.9") == trace_service.hash_ip("203.0.113.9")
    assert trace_service.hash_ip("203.0.113.9") != trace_service.hash_ip("203.0.113.10")


def _request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("10.0.0.5", 1234),
    }
    return Request(scope)


def test_client_ip_honours_trust_proxy_headers():
    settings = get_settings()
    original = settings.trust_proxy_headers
    try:
        settings.trust_proxy_headers = False
        assert get_client_ip(_request({"X-Forwarded-For": "198.51.100.7"})) == "10.0.0.5"
        settings.trust_proxy_headers = True
        assert get_client_ip(_request({"X-Forwarded-For": "198.51.100.7, 10.0.0.5"})) == (
            "198.51.100.7"
        )
    finally:
        settings.trust_proxy_headers = original


# ── 4. /health/detailed is owner only ────────────────────────────────────────


def test_health_detailed_is_owner_only(client, db):
    assert client.get("/api/v1/health/detailed").status_code == 401
    assert client.get("/api/v1/health/detailed", headers=_bearer(db, "consumer")).status_code == 403
    r = client.get("/api/v1/health/detailed", headers=_bearer(db, "owner"))
    assert r.status_code == 200 and "python_version" in r.json()["system"]


# ── 5. A failed pg_dump must never look like a successful backup ─────────────


def test_failed_pg_dump_raises_and_leaves_no_fake_backup(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("pg_dump")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="no backup was created"):
        backup_db.backup_postgres("postgresql://u:p@localhost:5432/x", tmp_path)
    assert list(tmp_path.iterdir()) == []


# ── 6. JWT secret strength outside local ─────────────────────────────────────


def test_short_jwt_secret_is_rejected_outside_local():
    with pytest.raises(ValidationError):
        Settings(environment="staging", jwt_secret="too-short")
    assert Settings(environment="staging", jwt_secret="x" * 40).jwt_secret == "x" * 40


# ── 7. Backup then restore works for both dump formats (PostgreSQL only) ─────


@pytest.mark.parametrize("compress", [True, False])
def test_postgres_backup_restore_roundtrip(tmp_path: Path, compress: bool):
    import os
    import shutil

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from scripts.restore_db import restore_backup
    from tests.conftest import _engine

    if _engine.dialect.name != "postgresql" or not shutil.which("pg_dump"):
        pytest.skip("Needs PostgreSQL and the pg_dump/pg_restore tools.")

    server = make_url(os.environ["TEST_DATABASE_URL"])
    names = {k: f"rt_{k}_{uuid.uuid4().hex[:6]}" for k in ("src", "dst")}
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            for n in names.values():
                conn.execute(text(f'CREATE DATABASE "{n}"'))
        src_url = server.set(database=names["src"])
        dst_url = server.set(database=names["dst"])
        src = create_engine(src_url)
        with src.begin() as conn:
            conn.execute(text("CREATE TABLE t (id int, name text)"))
            conn.execute(text("INSERT INTO t VALUES (1, 'a'), (2, 'b')"))
        src.dispose()

        def plain(u):
            return u.render_as_string(hide_password=False).replace("+psycopg", "")

        meta = backup_db.create_backup(
            db_url=plain(src_url), output_dir=tmp_path, compress=compress
        )
        restore_backup(Path(meta["backup_file"]), db_url=plain(dst_url))
        dst = create_engine(dst_url)
        with dst.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM t")).scalar() == 2
        dst.dispose()
    finally:
        with admin.connect() as conn:
            for n in names.values():
                conn.execute(text(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)'))
        admin.dispose()
