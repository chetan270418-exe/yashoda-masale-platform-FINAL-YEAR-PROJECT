"""Forecasting Engine & Chronological Evaluation (Decision D-015, Rules 66, 72).

Models:
1. Baseline 1: Seasonal Naive (yt = yt-12)
2. Baseline 2: Moving Average (window=3)
3. Candidate Model: Linear Trend with Seasonal Indices

Evaluation:
- Split by time (first 18 months train, last 6 months test). Never shuffled.
- Metrics: MAE, RMSE, MAPE.
"""

import math
from decimal import Decimal
from typing import Any

from forecasting.generator import MonthlyDemandRecord


def calculate_metrics(
    actuals: list[Decimal],
    predictions: list[Decimal],
) -> dict[str, Decimal]:
    """Calculate MAE, RMSE, and MAPE metrics."""
    if not actuals or len(actuals) != len(predictions):
        raise ValueError(
            "Actuals and predictions must be non-empty and matching length."
        )

    n = len(actuals)
    sum_abs_err = Decimal("0")
    sum_sq_err = Decimal("0")
    sum_ape = Decimal("0")

    for act, pred in zip(actuals, predictions, strict=True):
        err = pred - act
        abs_err = abs(err)
        sum_abs_err += abs_err
        sum_sq_err += err * err
        if act > Decimal("0"):
            sum_ape += (abs_err / act) * Decimal("100")

    mae = (sum_abs_err / Decimal(str(n))).quantize(Decimal("0.0001"))
    rmse = Decimal(str(math.sqrt(float(sum_sq_err / Decimal(str(n)))))).quantize(
        Decimal("0.0001")
    )
    mape = (sum_ape / Decimal(str(n))).quantize(Decimal("0.0001"))

    return {"mae": mae, "rmse": rmse, "mape": mape}


class ForecastingPipeline:
    """End-to-end forecasting pipeline with chronological evaluation."""

    def __init__(self, records: list[MonthlyDemandRecord]) -> None:
        self.records = sorted(records, key=lambda r: r.month_index)
        self.train = [r for r in self.records if r.is_train]
        self.test = [r for r in self.records if not r.is_train]

    def predict_seasonal_naive(self) -> list[Decimal]:
        """Baseline 1: Same month from previous year (t - 12)."""
        preds: list[Decimal] = []
        for r in self.test:
            prev_idx = r.month_index - 12
            prev_record = next(
                (x for x in self.records if x.month_index == prev_idx), None
            )
            val = prev_record.actual_quantity if prev_record else Decimal("500.0")
            preds.append(val)
        return preds

    def predict_moving_average(self, window: int = 3) -> list[Decimal]:
        """Baseline 2: Simple moving average of the preceding N months."""
        preds: list[Decimal] = []
        for r in self.test:
            prev_records = [
                x.actual_quantity
                for x in self.records
                if r.month_index - window <= x.month_index < r.month_index
            ]
            if prev_records:
                avg = sum(prev_records) / Decimal(str(len(prev_records)))
            else:
                avg = Decimal("500.0")
            preds.append(avg.quantize(Decimal("0.1")))
        return preds

    def predict_linear_trend_seasonal(self) -> list[Decimal]:
        """Candidate Model: Linear regression on trend index adjusted by month seasonality."""
        n = len(self.train)
        sum_x = sum(r.month_index for r in self.train)
        sum_y = sum(float(r.actual_quantity) for r in self.train)
        sum_xy = sum(r.month_index * float(r.actual_quantity) for r in self.train)
        sum_xx = sum(r.month_index * r.month_index for r in self.train)

        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_xx - sum_x * sum_x)
        intercept = (sum_y - slope * sum_x) / n

        # Estimate seasonal factors from train residuals
        month_factors: dict[int, list[float]] = {}
        for r in self.train:
            m = int(r.period.split("-")[1])
            trend_val = intercept + slope * r.month_index
            factor = float(r.actual_quantity) / max(trend_val, 1.0)
            month_factors.setdefault(m, []).append(factor)

        avg_factors = {m: sum(facts) / len(facts) for m, facts in month_factors.items()}

        preds: list[Decimal] = []
        for r in self.test:
            m = int(r.period.split("-")[1])
            base_trend = intercept + slope * r.month_index
            seas_mult = avg_factors.get(m, 1.0)
            pred = max(50.0, base_trend * seas_mult)
            preds.append(Decimal(str(round(pred, 1))))
        return preds

    def run_evaluations(self) -> dict[str, Any]:
        """Execute all models on test set and calculate honest metrics."""
        test_actuals = [r.actual_quantity for r in self.test]
        test_period = f"{self.test[0].period} to {self.test[-1].period}"

        # 1. Seasonal Naive
        sn_preds = self.predict_seasonal_naive()
        sn_metrics = calculate_metrics(test_actuals, sn_preds)

        # 2. Moving Average
        ma_preds = self.predict_moving_average()
        ma_metrics = calculate_metrics(test_actuals, ma_preds)

        # 3. Candidate: Linear Trend Seasonal
        lts_preds = self.predict_linear_trend_seasonal()
        lts_metrics = calculate_metrics(test_actuals, lts_preds)

        return {
            "test_period": test_period,
            "models": {
                "seasonal_naive": {
                    "metrics": sn_metrics,
                    "predictions": [float(p) for p in sn_preds],
                },
                "moving_average_3m": {
                    "metrics": ma_metrics,
                    "predictions": [float(p) for p in ma_preds],
                },
                "linear_trend_seasonal": {
                    "metrics": lts_metrics,
                    "predictions": [float(p) for p in lts_preds],
                },
            },
            "actuals": [float(a) for a in test_actuals],
            "periods": [r.period for r in self.test],
        }
