"""Seed default warehouses into the database.

Usage:
    python -m scripts.seed_inventory

Idempotent — skips warehouses that already exist by code.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_engine
from app.inventory.models import Warehouse

DEFAULT_WAREHOUSES = [
    {
        "code": "WH-CENTRAL-01",
        "name": "Central Warehouse - Pune",
        "is_primary": True,
        "is_active": True,
    },
    {
        "code": "WH-MUM-01",
        "name": "Regional Hub - Mumbai",
        "is_primary": False,
        "is_active": True,
    },
]


def seed_warehouses(session: Session) -> None:
    """Insert default warehouses if they don't already exist."""
    existing_codes = set(session.scalars(select(Warehouse.code)).all())
    for wh_data in DEFAULT_WAREHOUSES:
        if wh_data["code"] not in existing_codes:
            wh = Warehouse(**wh_data)
            session.add(wh)
            print(f"  + {wh.code} ({wh.name}) [primary={wh.is_primary}]")
        else:
            print(f"  ✓ {wh_data['code']} (exists)")
    session.commit()


if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker

    engine = get_engine()
    with sessionmaker(bind=engine)() as session:
        print("Seeding warehouses …")
        seed_warehouses(session)
        print("Done.")
