"""Seed the six default roles into the database.

Usage:
    python -m scripts.seed_roles

Idempotent — skips roles that already exist.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Role
from app.core.db import get_engine

ROLE_NAMES = [
    "owner",
    "factory_manager",
    "warehouse_manager",
    "distributor",
    "dealer",
    "consumer",
]


def seed_roles(session: Session) -> None:
    """Insert default roles if they don't already exist."""
    existing = set(session.scalars(select(Role.name)).all())
    for name in ROLE_NAMES:
        if name not in existing:
            session.add(Role(name=name))
            print(f"  + {name}")
        else:
            print(f"  ✓ {name} (exists)")
    session.commit()


if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker

    engine = get_engine()
    with sessionmaker(bind=engine)() as session:
        print("Seeding roles …")
        seed_roles(session)
        print("Done.")
