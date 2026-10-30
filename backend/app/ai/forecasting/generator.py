"""Synthetic sales demand data generator (Decision D-007, Rule 72).

Generates 24 months of monthly historical demand per variant:
demand = base * trend * season * festival * noise

Determinism (Rule 72): every variant is generated from a seed derived from the
base seed and the variant SKU, so the same SKU always reproduces the same
series while different SKUs do not share a noise pattern.

Realism: each variant gets its own seasonal amplitude and phase offset, so the
variants do not all share one identical seasonal shape. A shared shape would
make any forecasting model look better than it really is, because the model
only has to learn one global pattern instead of many.

The minimum order size below is a business rule of the demo dataset. It is
deliberately NOT reproduced inside the forecasting models: a model must not
assume any floor it was not told about.
"""

import random
import zlib
from dataclasses import dataclass
from decimal import Decimal

# Fixed random seed for reproducible demo and evaluation (Rule 72)
DEMO_RANDOM_SEED = 42

# Minimum monthly order size in the synthetic dataset (a business rule).
MIN_MONTHLY_UNITS = 50.0

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


def variant_seed(variant_sku: str, seed: int = DEMO_RANDOM_SEED) -> int:
    """Derive a stable per-variant seed from the SKU and the base seed.

    CRC32 is used rather than ``hash()`` because ``hash()`` is salted per
    process (PYTHONHASHSEED), which would break the reproducibility that Rule
    72 requires.
    """
    crc = zlib.crc32(variant_sku.encode("utf-8")) & 0xFFFFFFFF
    return (seed + crc) % (2**32)


def generate_synthetic_history(
    variant_sku: str = "GM-250G",
    base_demand: float = 500.0,
    seed: int = DEMO_RANDOM_SEED,
) -> list[MonthlyDemandRecord]:
    """Generate 24 months of synthetic historical sales data with labelled split."""
    rng = random.Random(variant_seed(variant_sku, seed))

    # Per-variant seasonal profile.
    # amplitude scales how strong this variant's seasonal swing is,
    # phase_offset shifts which calendar months carry the peak.
    amplitude = rng.uniform(0.55, 1.45)
    phase_offset = rng.randint(0, 11)
    # Festival demand is real for every spice, but its strength differs.
    festival_strength = rng.uniform(0.0, 0.25)

    records: list[MonthlyDemandRecord] = []

    for idx, period in enumerate(MONTH_NAMES):
        # 1-indexed month number
        month_num = int(period.split("-")[1])

        # Trend factor: slow growth from 1.0 to 1.15 over 24 months
        trend = 1.0 + (0.15 * (idx / 23.0))

        # Seasonal factor on this variant's own phase-shifted calendar month.
        shifted_month = ((month_num - 1 - phase_offset) % 12) + 1
        base_season = SEASONAL_WEIGHTS.get(shifted_month, 1.0)
        season = 1.0 + (base_season - 1.0) * amplitude

        # Festival bump stays on the calendar month (Diwali is Diwali for every
        # spice) but its strength varies per variant.
        festival = 1.0 + (festival_strength if month_num in (10, 11) else 0.0)

        # Pseudo-random noise +/- 8%
        noise = 1.0 + rng.uniform(-0.08, 0.08)

        qty = max(MIN_MONTHLY_UNITS, base_demand * trend * season * festival * noise)
        actual_dec = Decimal(str(round(qty, 1)))

        # Chronological split: First 18 months train, last 6 months test (held-out)
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
