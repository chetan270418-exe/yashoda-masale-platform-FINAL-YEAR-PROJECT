"""Smoke test: apply the whole Alembic chain to a scratch SQLite database.

The conftest builds the test schema from the ORM metadata, not from the
migrations, so the migrations are never exercised by the test suite. This
script checks that the chain 0001 -> 0008 applies cleanly, and that the tables
it creates are the ones the application expects.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(r"D:\final year project\backend")

EXPECTED_TABLES = {
    "users",
    "roles",
    "audit_logs",
    "categories",
    "products",
    "product_variants",
    "price_lists",
    "price_list_items",
    "warehouses",
    "production_batches",
    "orders",
    "order_items",
    "inventory_balances",
    "stock_movements",
    "stock_reservations",
    "ai_tool_runs",
    "sales_forecasts",
    "forecast_evaluations",
    "order_drafts",
    "suppliers",
    "raw_materials",
    "material_lots",
    "material_receipts",
    "material_receipt_lines",
    "production_plans",
    "production_stages",
    "quality_parameters",
    "quality_checks",
    "quality_decisions",
    "batch_events",
    "verification_scans",
    "invoices",
    "shipments",
    "shipment_events",
}


def run(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=BACKEND, env=env, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)


def main() -> int:
    db_path = Path(tempfile.gettempdir()) / f"alembic_smoke_{os.getpid()}.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}

    code, output = run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
    print("--- alembic upgrade head ---")
    print(output.strip()[-3000:] or "(no output)")
    if code != 0:
        print(f"FAILED with exit code {code}")
        return 1

    conn = sqlite3.connect(db_path)
    rows = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    tables = {t for t in rows if not t.startswith("sqlite")}

    print(f"\n--- created {len(tables)} tables ---")
    missing = EXPECTED_TABLES - tables
    extra = tables - EXPECTED_TABLES - {"alembic_version"}

    if missing:
        print(f"MISSING ({len(missing)}): {sorted(missing)}")
    if extra:
        print(f"EXTRA ({len(extra)}): {sorted(extra)}")
    if not missing and not extra:
        print("All expected tables present, no unexpected tables.")

    # The batch status CHECK must be the canonical list, never the legacy one.
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='production_batches'").fetchone()[
        0
    ]
    print("\n--- production_batches status constraint ---")
    for line in ddl.splitlines():
        if "status" in line and "CHECK" in line.upper() or "status IN" in line:
            print(" ", line.strip())
    legacy = [v for v in ("'hold'", "'recalled'") if v in ddl]
    print(f"  legacy values still present: {legacy if legacy else 'none'}")

    conn.close()
    db_path.unlink(missing_ok=True)
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
