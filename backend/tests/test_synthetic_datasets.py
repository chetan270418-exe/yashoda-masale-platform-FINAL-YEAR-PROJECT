"""Automated validation test for synthetic datasets in docs/synthetic-data/.

Enforces Acceptance Criteria for R5:
- All required CSVs and documentation exist.
- Datasets are clearly labelled as synthetic per Rule 44 and Rule 89.
- Data structures align with Milestone M5 models, price snapshots (B8), and Decimal types (B12).
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path


def _get_synthetic_data_dir() -> Path:
    # Project root is parent of backend
    root = Path(__file__).resolve().parent.parent.parent
    return root / "docs" / "synthetic-data"


def test_synthetic_dataset_files_and_docs_exist():
    """Verify presence of all synthetic data files and documentation."""
    data_dir = _get_synthetic_data_dir()
    assert data_dir.exists(), f"Directory not found: {data_dir}"

    expected_files = [
        "products.csv",
        "variants.csv",
        "suppliers.csv",
        "warehouses.csv",
        "production_batches.csv",
        "inventory.csv",
        "inventory_balances.csv",
        "stock_movements.csv",
        "dealers.csv",
        "orders.csv",
        "order_items.csv",
        "sales_history.csv",
        "sales_history_daily.csv",
        "manifest.json",
        "DATASET_SPECIFICATION.md",
    ]
    for filename in expected_files:
        filepath = data_dir / filename
        assert filepath.exists(), f"Missing required synthetic file: {filename}"
        assert filepath.stat().st_size > 0, f"Synthetic file is empty: {filename}"


def test_manifest_disclaimer_rule_44_and_89():
    """Verify manifest.json contains mandatory synthetic disclaimer."""
    data_dir = _get_synthetic_data_dir()
    manifest_path = data_dir / "manifest.json"
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest.get("dataset_type") == "synthetic_demo_data"
    assert "seed" in manifest
    assert "not actual sponsor data" in manifest.get("warning", "").lower()


def test_synthetic_batches_invariants_b1_b2():
    """Verify production batches enforce quality status and shelf-life dates."""
    data_dir = _get_synthetic_data_dir()
    batches_path = data_dir / "production_batches.csv"

    valid_statuses = {"approved", "held", "rejected"}
    valid_release_states = {"released", "held", "hold", "rejected"}

    with open(batches_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) > 0
    for r in rows[:50]:  # spot check sample
        assert r["quality_status"].lower() in valid_statuses
        assert r["status"].lower() in valid_release_states
        assert r["is_synthetic"].lower() == "true"
        assert len(r["batch_code"]) > 0
        assert len(r["expires_on"]) == 10  # YYYY-MM-DD


def test_synthetic_inventory_balances_invariant_b4():
    """Verify on_hand >= 0, reserved >= 0, and reserved <= on_hand."""
    data_dir = _get_synthetic_data_dir()
    inv_path = data_dir / "inventory.csv"

    with open(inv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) > 0
    for r in rows:
        on_hand = Decimal(r["on_hand_qty"])
        reserved = Decimal(r["reserved_qty"])
        assert on_hand >= Decimal("0"), f"Negative on_hand: {on_hand}"
        assert reserved >= Decimal("0"), f"Negative reserved: {reserved}"
        assert reserved <= on_hand, f"Reserved exceeds on_hand: {reserved} > {on_hand}"


def test_synthetic_order_items_m5_snapshots_b8_b12():
    """Verify order items store immutable price snapshots with decimal precision."""
    data_dir = _get_synthetic_data_dir()
    items_path = data_dir / "order_items.csv"

    with open(items_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) > 0
    for r in rows[:100]:  # spot check first 100 items
        qty = Decimal(r["quantity"])
        snapshot = Decimal(r["unit_price_inr"])
        line_total = Decimal(r["line_total_inr"])

        assert qty > Decimal("0")
        assert snapshot >= Decimal("0")
        assert line_total >= Decimal("0")
