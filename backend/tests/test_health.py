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
