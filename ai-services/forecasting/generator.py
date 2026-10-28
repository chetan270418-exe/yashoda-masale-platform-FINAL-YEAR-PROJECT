"""Synthetic sales demand data generator (Decision D-007, Rule 72).

Generates 24 months of monthly historical demand per variant:
demand = base * trend * season * festival * noise
Uses fixed random seed 42 for exact determinism.
"""

import random
from dataclasses import dataclass
from decimal import Decimal

# Fixed random seed for reproducible demo and evaluation (Rule 72)
DEMO_RANDOM_SEED = 42

MONTH_NAMES = [
    "2024-11",
    "2024-12",
    "2025-01",
    "2025-02",
    "2025-03",
    "2025-04",
    "2025-05",
    "2025-06",
    "2025-07",
    "2025-08",
    "2025-09",
    "2025-10",
    "2025-11",
    "2025-12",
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
    "2026-09",
    "2026-10",
]

# Monthly seasonal multipliers (higher in winter/wedding months)
SEASONAL_WEIGHTS = {
    1: 1.25,  # Jan: Winter peak, Makar Sankranti
    2: 1.10,  # Feb: Marriage season
    3: 0.95,  # Mar
    4: 0.85,  # Apr: Summer lull
    5: 0.80,  # May: Summer lull
    6: 0.90,  # Jun: Monsoon onset
    7: 0.95,  # Jul: Monsoon
    8: 1.15,  # Aug: Shravan / Raksha Bandhan
    9: 1.20,  # Sep: Ganesh Chaturthi
    10: 1.35,  # Oct: Navratri / Dussehra
    11: 1.40,  # Nov: Diwali peak
    12: 1.30,  # Dec: Winter peak
}


@dataclass
class MonthlyDemandRecord:
    variant_sku: str
    period: str
    month_index: int
    actual_quantity: Decimal
    is_train: bool  # Chronological split: first 18 train, last 6 test


def generate_synthetic_history(
    variant_sku: str = "GM-250G",
    base_demand: float = 500.0,
    seed: int = DEMO_RANDOM_SEED,
) -> list[MonthlyDemandRecord]:
    """Generate 24 months of synthetic historical sales data with labelled split."""
    rng = random.Random(seed)
    records: list[MonthlyDemandRecord] = []

    for idx, period in enumerate(MONTH_NAMES):
        month_num = int(period.split("-")[1])
        trend = 1.0 + (0.15 * (idx / 23.0))
        season = SEASONAL_WEIGHTS.get(month_num, 1.0)
        festival = 1.10 if month_num in (10, 11) else 1.0
        noise = 1.0 + rng.uniform(-0.08, 0.08)

        qty = max(50.0, base_demand * trend * season * festival * noise)
        actual_dec = Decimal(str(round(qty, 1)))
        is_train = idx < 18

        records.append(
            MonthlyDemandRecord(
                variant_sku=variant_sku,
                period=period,
                month_index=idx,
                actual_quantity=actual_dec,
                is_train=is_train,
            )
        )

    return records
