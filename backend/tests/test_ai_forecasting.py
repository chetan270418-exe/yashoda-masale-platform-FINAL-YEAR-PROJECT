"""Tests for Forecasting models, chronological evaluation, and reorder math
(Rules 66, 72, Decision D-015)."""

from datetime import date, timedelta
from decimal import Decimal

from app.ai.forecasting.engine import ForecastingPipeline, calculate_metrics
from app.ai.forecasting.generator import generate_synthetic_history
from app.ai.forecasting.reorder import compute_batch_expiry_risk, compute_reorder_recommendation


def test_synthetic_data_generator_determinism():
    """Generator with fixed seed must be 100% deterministic (Rule 72)."""
    h1 = generate_synthetic_history(seed=42)
    h2 = generate_synthetic_history(seed=42)

    assert len(h1) == 24
    assert [r.actual_quantity for r in h1] == [r.actual_quantity for r in h2]

    # Verify chronological split: 18 train, 6 test
    train_count = sum(1 for r in h1 if r.is_train)
    test_count = sum(1 for r in h1 if not r.is_train)
    assert train_count == 18
    assert test_count == 6


def test_calculate_metrics_math():
    """Metrics calculation must compute exact mathematical values."""
    actuals = [Decimal("100.0"), Decimal("200.0")]
    preds = [Decimal("110.0"), Decimal("180.0")]

    metrics = calculate_metrics(actuals, preds)
    # MAE = (10 + 20) / 2 = 15.0
    assert metrics["mae"] == Decimal("15.0000")
    # RMSE = sqrt((100 + 400) / 2) = sqrt(250) ~ 15.8114
    assert metrics["rmse"] == Decimal("15.8114")
    # MAPE = ((10/100 + 20/200) / 2) * 100 = 10.0%
    assert metrics["mape"] == Decimal("10.0000")


def test_forecasting_pipeline_evaluation():
    """Pipeline executes models over chronological split without data leakage (Decision D-015)."""
    history = generate_synthetic_history(seed=42)
    pipeline = ForecastingPipeline(history)

    results = pipeline.run_evaluations()

    assert "test_period" in results
    assert "seasonal_naive" in results["models"]
    assert "moving_average_3m" in results["models"]
    assert "linear_trend_seasonal" in results["models"]

    for _model_name, info in results["models"].items():
        assert "mae" in info["metrics"]
        assert "rmse" in info["metrics"]
        assert len(info["predictions"]) == 6


def test_compute_reorder_recommendation_math():
    """Reorder recommendations must compute visible inputs transparently (Rule 129)."""
    # Available = 100, monthly demand = 600 (daily = 20)
    # lead time = 14 days (280 units), safety stock = 7 days (140 units)
    # reorder point = 280 + 140 = 420.
    # Since 100 <= 420, reorder needed!
    # target cover = 45 days (900 units). Recommended order = 900 - 100 = 800.
    rec = compute_reorder_recommendation(
        variant_id="test-var-1",
        sku="GM-250G",
        product_name="Garam Masala",
        available_stock=100.0,
        monthly_forecast=600.0,
        lead_time_days=14,
        safety_days=7,
        target_cover_days=45,
    )

    assert rec.daily_demand == 20.0
    assert rec.safety_stock == 140.0
    assert rec.reorder_point == 420.0
    assert rec.reorder_needed is True
    assert rec.recommended_order_quantity == 800.0


def test_compute_reorder_recommendation_not_needed():
    """When stock is above reorder point, recommended quantity is 0."""
    rec = compute_reorder_recommendation(
        variant_id="test-var-2",
        sku="TP-100G",
        product_name="Turmeric",
        available_stock=500.0,
        monthly_forecast=600.0,
        lead_time_days=14,
        safety_days=7,
    )
    assert rec.reorder_needed is False
    assert rec.recommended_order_quantity == 0.0


def test_compute_batch_expiry_risk_scoring():
    """Batch expiry risk accurately identifies batches exceeding projected sales."""
    today = date(2026, 11, 1)

    # Batch expiring in 10 days, 200 units available, daily demand = 5 units (50 consumed).
    # 150 units will be unsold before expiry -> HIGH risk!
    risk = compute_batch_expiry_risk(
        batch_code="B-2026-001",
        expiry_date=today + timedelta(days=10),
        available_stock=200.0,
        monthly_forecast=150.0,  # daily = 5
        reference_date=today,
    )
    assert risk["risk_level"] == "HIGH"
    assert risk["estimated_unsold_quantity"] == 150.0
    assert "PROMOTION" in risk["suggested_action"]
