"""Test fixtures: in-memory SQLite database with seeded roles."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.models import Role, User
from app.auth.security import hash_password
from app.catalog.models import PriceList
from app.core.db import Base, get_db
from app.main import app

# SQLite in-memory with StaticPool so all sessions share one connection
_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


# Enable FK enforcement on every new SQLite connection
@event.listens_for(_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_TestSession = sessionmaker(bind=_engine, expire_on_commit=False)

ROLE_NAMES = [
    "owner",
    "factory_manager",
    "warehouse_manager",
    "distributor",
    "dealer",
    "consumer",
]


@pytest.fixture(autouse=True)
def _setup_db():
    """Create all tables before each test and drop them afterwards."""
    Base.metadata.create_all(bind=_engine)
    # Seed roles and price lists
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
    """FastAPI TestClient wired to the in-memory SQLite DB."""
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
        email=email.lower(),
        password_hash=hash_password(password),
        full_name=full_name,
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
