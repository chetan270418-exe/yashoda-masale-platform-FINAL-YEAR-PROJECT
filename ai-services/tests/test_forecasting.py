"""Tests for standalone forecasting engine and reorder intelligence."""

from datetime import date, timedelta
from decimal import Decimal

from forecasting.engine import ForecastingPipeline, calculate_metrics
from forecasting.generator import generate_synthetic_history
from forecasting.reorder import (
    compute_batch_expiry_risk,
    compute_reorder_recommendation,
)


def test_synthetic_data_generator_determinism():
    h1 = generate_synthetic_history(seed=42)
    h2 = generate_synthetic_history(seed=42)
    assert len(h1) == 24
    assert [r.actual_quantity for r in h1] == [r.actual_quantity for r in h2]


def test_chronological_train_test_split():
    history = generate_synthetic_history(seed=42)
    train_records = [r for r in history if r.is_train]
    test_records = [r for r in history if not r.is_train]
    assert len(train_records) == 18
    assert len(test_records) == 6


def test_calculate_metrics_accuracy():
    actuals = [Decimal("100.0"), Decimal("200.0")]
    preds = [Decimal("110.0"), Decimal("190.0")]
    metrics = calculate_metrics(actuals, preds)
    assert metrics["mae"] == Decimal("10.0000")
    assert metrics["rmse"] == Decimal("10.0000")
    assert metrics["mape"] == Decimal("7.5000")


def test_forecasting_pipeline_evaluation():
    history = generate_synthetic_history(seed=42)
    pipeline = ForecastingPipeline(history)
    results = pipeline.run_evaluations()

    assert "seasonal_naive" in results["models"]
    assert "moving_average_3m" in results["models"]
    assert "linear_trend_seasonal" in results["models"]

    for _model_name, info in results["models"].items():
        assert "mae" in info["metrics"]
        assert "rmse" in info["metrics"]
        assert "mape" in info["metrics"]
        assert len(info["predictions"]) == 6


def test_reorder_recommendation_math():
    rec = compute_reorder_recommendation(
        variant_id="v1",
        sku="GM-250G",
        product_name="Garam Masala 250g",
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


def test_batch_expiry_risk():
    today = date(2026, 10, 28)
    risk = compute_batch_expiry_risk(
        batch_code="B1",
        expiry_date=today + timedelta(days=15),
        available_stock=500.0,
        monthly_forecast=300.0,
        reference_date=today,
    )
    assert risk["risk_level"] in ("HIGH", "MEDIUM")
    assert (
        risk["potential_unsold_quantity"]
        if "potential_unsold_quantity" in risk
        else risk["estimated_unsold_quantity"] > 0
    )
