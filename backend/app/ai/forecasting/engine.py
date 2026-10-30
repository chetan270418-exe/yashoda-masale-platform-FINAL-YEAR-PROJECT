"""Forecasting Engine & Chronological Evaluation (Decision D-015, Rules 66, 72).

Models:
1. Baseline 1: Seasonal Naive (y_t = y_{t-12})
2. Baseline 2: Moving Average (window = 3)
3. Candidate Model: Linear Trend with Seasonal Indices

Evaluation:
- Split by time (first 18 months train, last 6 months test). Never shuffled.
- Every prediction is built from a history window that ends before the month
  being predicted, so no held-out actual ever leaks into a forecast.
- Metrics: MAE, RMSE, MAPE and MASE.
- MASE scales a model's MAE by the in-sample MAE of the seasonal naive
  baseline::

      MASE = MAE(model) / MAE(seasonal naive, in-sample)

  MASE below 1 means the model beat the seasonal naive baseline. MASE is
  scale-free, so it can be compared across products of different volumes,
  which plain MAE cannot do.
- Rolling-origin backtesting refits every model at several origins. A single
  hold-out split can be a lucky cut, and with only 18 training months some
  calendar months carry a single observation, so the rolling result is the
  honest one.
"""

import math
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.ai.forecasting.generator import MonthlyDemandRecord
from app.ai.models import ForecastEvaluation, SalesForecast

SEASONAL_LAG = 12
MOVING_AVERAGE_WINDOW = 3
EPSILON = Decimal("0.0001")

# A model is a function of (history, target month, fallback value).
ModelFn = Callable[[list[MonthlyDemandRecord], MonthlyDemandRecord, Decimal], Decimal]

MODEL_NAMES = ("seasonal_naive", "moving_average_3m", "linear_trend_seasonal")


def calculate_metrics(
    actuals: list[Decimal],
    predictions: list[Decimal],
    seasonal_naive_train_mae: Decimal | None = None,
) -> dict[str, Decimal | None]:
    """Calculate MAE, RMSE, MAPE and, when a baseline is supplied, MASE.

    MAPE is averaged over the months whose actual demand is above zero only.
    A month with zero actual demand makes the percentage error undefined, so
    those months are excluded from the average instead of silently diluting it.

    ``seasonal_naive_train_mae`` is the in-sample MAE of the seasonal naive
    baseline. When supplied, MASE is returned as well.
    """
    if not actuals or len(actuals) != len(predictions):
        raise ValueError("Actuals and predictions must be non-empty and matching length.")

    n = len(actuals)
    sum_abs_err = Decimal("0")
    sum_sq_err = Decimal("0")
    sum_ape = Decimal("0")
    non_zero_actuals = 0

    for act, pred in zip(actuals, predictions, strict=True):
        err = pred - act
        abs_err = abs(err)
        sum_abs_err += abs_err
        sum_sq_err += err * err
        if act > Decimal("0"):
            non_zero_actuals += 1
            sum_ape += (abs_err / act) * Decimal("100")

    mae = (sum_abs_err / Decimal(str(n))).quantize(EPSILON)
    rmse = Decimal(str(math.sqrt(float(sum_sq_err / Decimal(str(n)))))).quantize(EPSILON)
    mape = (
        (sum_ape / Decimal(str(non_zero_actuals))).quantize(EPSILON) if non_zero_actuals else None
    )

    mase: Decimal | None = None
    if seasonal_naive_train_mae is not None and seasonal_naive_train_mae > Decimal("0"):
        mase = (mae / seasonal_naive_train_mae).quantize(EPSILON)

    return {"mae": mae, "rmse": rmse, "mape": mape, "mase": mase}


def history_mean(history: list[MonthlyDemandRecord]) -> Decimal:
    """Mean demand over the history window."""
    if not history:
        return Decimal("0")
    total = sum((h.actual_quantity for h in history), Decimal("0"))
    return total / Decimal(str(len(history)))


def seasonal_naive_mae(history: list[MonthlyDemandRecord]) -> Decimal | None:
    """In-sample MAE of the seasonal naive baseline: the MASE denominator.

    For every month in the window that has a matching month 12 periods earlier
    inside the same window, the absolute error of the one-year-lag forecast.
    Returns None when the window is too short to form a single pair.
    """
    by_index = {r.month_index: r.actual_quantity for r in history}
    errors = [
        abs(r.actual_quantity - by_index[r.month_index - SEASONAL_LAG])
        for r in history
        if r.month_index - SEASONAL_LAG in by_index
    ]
    if not errors:
        return None
    return (sum(errors, Decimal("0")) / Decimal(str(len(errors)))).quantize(EPSILON)


def predict_seasonal_naive(
    history: list[MonthlyDemandRecord], target: MonthlyDemandRecord, fallback: Decimal
) -> Decimal:
    """Baseline 1: the same calendar month from 12 periods earlier.

    When that month is not in the history window the seasonal naive forecast is
    undefined. The history mean is used as a documented fallback rather than an
    arbitrary constant.
    """
    prev = next((h for h in history if h.month_index == target.month_index - SEASONAL_LAG), None)
    return prev.actual_quantity if prev else fallback


def predict_moving_average(
    history: list[MonthlyDemandRecord],
    target: MonthlyDemandRecord,
    fallback: Decimal,
    window: int = MOVING_AVERAGE_WINDOW,
) -> Decimal:
    """Baseline 2: mean of the preceding `window` observed months."""
    values = [
        h.actual_quantity
        for h in history
        if target.month_index - window <= h.month_index < target.month_index
    ]
    if not values:
        return fallback
    return (sum(values, Decimal("0")) / Decimal(str(len(values)))).quantize(Decimal("0.1"))


def predict_linear_trend_seasonal(
    history: list[MonthlyDemandRecord], target: MonthlyDemandRecord, fallback: Decimal
) -> Decimal:
    """Candidate model: linear trend on month index, adjusted by seasonal indices.

    The seasonal indices are estimated from the residuals of the in-window trend
    fit only, so no future actual is ever used.
    """
    if len(history) < 2:
        return fallback

    n = len(history)
    sum_x = sum(h.month_index for h in history)
    sum_y = sum(float(h.actual_quantity) for h in history)
    sum_xy = sum(h.month_index * float(h.actual_quantity) for h in history)
    sum_xx = sum(h.month_index * h.month_index for h in history)
    denominator = n * sum_xx - sum_x * sum_x
    if denominator == 0:
        return fallback

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n

    # Seasonal index per calendar month, estimated from in-window residuals.
    factors: dict[int, list[float]] = {}
    for h in history:
        calendar_month = int(h.period.split("-")[1])
        trend_val = intercept + slope * h.month_index
        factor = float(h.actual_quantity) / max(trend_val, 1.0)
        factors.setdefault(calendar_month, []).append(factor)
    avg_factors = {m: sum(v) / len(v) for m, v in factors.items()}

    calendar_month = int(target.period.split("-")[1])
    seasonal = avg_factors.get(calendar_month, 1.0)
    base_trend = intercept + slope * target.month_index
    # Demand cannot be negative, but the model must not assume any minimum
    # order size: the lower bound is zero, not a constant copied from the
    # data generator.
    pred = max(0.0, base_trend * seasonal)
    return Decimal(str(round(pred, 1)))


MODELS: dict[str, ModelFn] = {
    "seasonal_naive": predict_seasonal_naive,
    "moving_average_3m": predict_moving_average,
    "linear_trend_seasonal": predict_linear_trend_seasonal,
}


class ForecastingPipeline:
    """End-to-end forecasting pipeline with chronological evaluation."""

    def __init__(self, records: list[MonthlyDemandRecord]) -> None:
        self.records = sorted(records, key=lambda r: r.month_index)
        self.train = [r for r in self.records if r.is_train]
        self.test = [r for r in self.records if not r.is_train]

    def seasonal_naive_train_mae(self) -> Decimal | None:
        """MASE denominator: in-sample MAE of the seasonal naive baseline."""
        return seasonal_naive_mae(self.train)

    def _forecast_block(
        self, history: list[MonthlyDemandRecord], targets: list[MonthlyDemandRecord]
    ) -> dict[str, list[Decimal]]:
        """Forecast every target from one fixed history window."""
        fallback = history_mean(history)
        return {
            name: [model(history, t, fallback) for t in targets] for name, model in MODELS.items()
        }

    def run_evaluations(
        self,
        db: Session | None = None,
        variant_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Evaluate every model on the held-out test block.

        The whole test block is forecast from the training window alone, which
        is the strict "forecast the next N months" reading of the split. No
        held-out actual is used to build any prediction.
        """
        test_actuals = [r.actual_quantity for r in self.test]
        test_period = f"{self.test[0].period} to {self.test[-1].period}"

        predictions = self._forecast_block(self.train, self.test)
        sn_train_mae = self.seasonal_naive_train_mae()

        results: dict[str, Any] = {
            "test_period": test_period,
            "models": {},
            "actuals": [float(a) for a in test_actuals],
            "periods": [r.period for r in self.test],
            "seasonal_naive_train_mae": float(sn_train_mae) if sn_train_mae is not None else None,
        }

        for name, preds in predictions.items():
            metrics = calculate_metrics(test_actuals, preds, sn_train_mae)
            results["models"][name] = {
                "metrics": metrics,
                "predictions": [float(p) for p in preds],
            }

        if db:
            for model_name, data in results["models"].items():
                m = data["metrics"]
                eval_row = ForecastEvaluation(
                    model=model_name,
                    variant_id=variant_id,
                    mae=m["mae"],
                    rmse=m["rmse"],
                    mape=m["mape"],
                    mase=m["mase"],
                    test_period=test_period,
                    notes=f"Chronological evaluation across {len(self.test)} test months.",
                )
                db.add(eval_row)

                for p_period, p_val in zip(results["periods"], data["predictions"], strict=True):
                    if variant_id:
                        db.add(
                            SalesForecast(
                                variant_id=variant_id,
                                period=p_period,
                                model=model_name,
                                predicted_quantity=Decimal(str(p_val)),
                            )
                        )

            db.commit()

        return results

    def run_rolling_origin(
        self,
        min_train_months: int = SEASONAL_LAG,
        horizon: int = 3,
        step: int = 3,
    ) -> dict[str, Any]:
        """Rolling-origin backtesting.

        Each origin refits every model on the data available up to that point
        and forecasts the next `horizon` months. This shows whether the single
        hold-out split was a lucky cut.
        """
        origins: list[dict[str, Any]] = []

        for origin in range(min_train_months, len(self.records) - horizon + 1, step):
            history = [r for r in self.records if r.month_index < origin]
            targets = [r for r in self.records if origin <= r.month_index < origin + horizon]
            if not history or not targets:
                continue

            actuals = [t.actual_quantity for t in targets]
            preds = self._forecast_block(history, targets)
            sn_mae = seasonal_naive_mae(history)

            origins.append(
                {
                    "origin": origin,
                    "train_end_period": history[-1].period,
                    "train_months": len(history),
                    "target_periods": [t.period for t in targets],
                    "actuals": [float(a) for a in actuals],
                    "models": {
                        name: {
                            "metrics": {
                                k: (float(v) if v is not None else None)
                                for k, v in calculate_metrics(actuals, p, sn_mae).items()
                            },
                            "predictions": [float(x) for x in p],
                        }
                        for name, p in preds.items()
                    },
                }
            )

        summary: dict[str, Any] = {}
        for name in MODELS:
            maes = [o["models"][name]["metrics"]["mae"] for o in origins]
            mases = [
                o["models"][name]["metrics"]["mase"]
                for o in origins
                if o["models"][name]["metrics"]["mase"] is not None
            ]
            summary[name] = {
                "mean_mae": (sum(maes) / len(maes)) if maes else None,
                "mean_mase": (sum(mases) / len(mases)) if mases else None,
                "origins": len(origins),
            }

        return {
            "origins": origins,
            "summary": summary,
            "min_train_months": min_train_months,
            "horizon": horizon,
            "step": step,
        }
