"""Database engine and session dependency."""

import uuid as _uuid
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import String, TypeDecorator, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class UuidStr(TypeDecorator):
    """Store UUID as CHAR(36) so it works identically on PostgreSQL and SQLite."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is not None:
            return str(value)
        return value

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is not None:
            return _uuid.UUID(value)
        return value


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    type_annotation_map = {
        _uuid.UUID: UuidStr,
    }


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
