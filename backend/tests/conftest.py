"""Test fixtures: supports PostgreSQL in CI / Docker, with in-memory SQLite fallback."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.auth.dependencies import login_rate_limiter
from app.auth.models import Role, User
from app.auth.security import hash_password
from app.catalog.models import PriceList
from app.core.db import Base, get_db
from app.main import app

ROLE_NAMES = [
    "owner",
    "factory",
    "warehouse",
    "distributor",
    "dealer",
    "consumer",
]


def _build_test_engine():
    """Use PostgreSQL if configured (e.g. CI or local Docker), else in-memory SQLite."""
    pg_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if "postgresql" in pg_url:
        try:
            pg_engine = create_engine(pg_url, poolclass=NullPool)
            with pg_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return pg_engine
        except Exception:
            pass

    sqlite_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(sqlite_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return sqlite_engine


_engine = _build_test_engine()
_TestSession = sessionmaker(bind=_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _setup_db():
    """Create all tables before each test and drop them afterwards."""
    login_rate_limiter.reset()
    Base.metadata.create_all(bind=_engine)

    # Seed canonical 6 roles and 3 price lists
    session = _TestSession()
    for name in ROLE_NAMES:
        session.add(Role(name=name))
    for name in ["MRP", "DEALER", "DISTRIBUTOR"]:
        session.add(PriceList(name=name))
    session.commit()
    session.close()

    yield

    Base.metadata.drop_all(bind=_engine)


def _override_get_db():
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def db() -> Session:
    """Provide a DB session for direct repository/service calls in tests."""
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    """FastAPI TestClient wired to the test DB."""
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_user(
    db: Session,
    *,
    email: str = "test@example.com",
    password: str = "testpass123",
    full_name: str = "Test User",
    role_name: str = "consumer",
) -> User:
    """Helper: create a user directly in the DB for test setup."""
    from app.auth.repository import get_role_by_name

    role = get_role_by_name(db, role_name)
    assert role is not None, f"Role '{role_name}' not found. Did you seed?"
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
