from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.db import get_db
from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_request_id_header_present():
    r = client.get("/health")
    assert r.headers["X-Request-ID"]


def test_request_id_is_echoed():
    r = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"


def test_unknown_route_uses_standard_error_format():
    r = client.get("/nope")
    assert r.status_code == 404
    body = r.json()
    assert set(body) == {"code", "message", "details"}
    assert body["code"] == "HTTP_404"


def test_db_health_reports_503_in_standard_format_when_db_down():
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("down"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        r = client.get("/api/v1/health/db")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json()["code"] == "DB_UNAVAILABLE"


def test_settings_refuses_default_secret_in_production():
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="JWT_SECRET must be set"):
        Settings(environment="production", jwt_secret="dev-secret-change-in-production-key-32")


def test_settings_refuses_empty_secret_in_production():
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="JWT_SECRET must be set"):
        Settings(environment="staging", jwt_secret="")


def test_settings_allows_valid_secret_in_production():
    from app.core.config import Settings

    s = Settings(environment="production", jwt_secret="a-very-long-production-secret-1234567890")
    assert s.jwt_secret == "a-very-long-production-secret-1234567890"


def test_settings_allows_dev_secret_in_local():
    from app.core.config import Settings

    s = Settings(environment="local", jwt_secret="dev-secret-change-in-production-key-32")
    assert s.jwt_secret == "dev-secret-change-in-production-key-32"


def test_health_live_probe():
    r = client.get("/api/v1/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"
    assert "timestamp" in r.json()


def test_health_ready_probe():
    r = client.get("/api/v1/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert r.json()["database"] == "connected"


def test_health_ready_probe_fails_when_db_down():
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("down"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        r = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json()["code"] == "DB_UNAVAILABLE"


def test_health_detailed_requires_authentication():
    r = client.get("/api/v1/health/detailed")
    assert r.status_code == 401
