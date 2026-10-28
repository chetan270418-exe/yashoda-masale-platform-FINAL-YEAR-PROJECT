"""Alembic migration test: verifies upgrade head, downgrade base, and re-upgrade head."""

import os
import tempfile

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_downgrade_roundtrip():
    """Verify that all migrations apply cleanly, downgrade cleanly, and re-apply."""
    # Use a clean temporary SQLite database for migration file verification
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        # 1. Run upgrade head
        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        expected_tables = {
            "roles",
            "users",
            "audit_logs",
            "categories",
            "products",
            "product_variants",
            "price_lists",
            "price_list_items",
            "alembic_version",
        }
        assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

        # 2. Test downgrade to base
        command.downgrade(cfg, "base")
        inspector_after_down = inspect(engine)
        remaining_tables = set(inspector_after_down.get_table_names()) - {"alembic_version"}
        assert len(remaining_tables) == 0, f"Tables left after downgrade: {remaining_tables}"

        # 3. Test re-upgrade to head
        command.upgrade(cfg, "head")
        inspector_after_up = inspect(engine)
        recreated_tables = set(inspector_after_up.get_table_names())
        assert expected_tables.issubset(recreated_tables)

    finally:
        engine.dispose()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass
