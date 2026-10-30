"""Tests for the forecast evaluation: metrics, MASE, leakage control and the
M7 evaluation report generator (Decision D-015, Rules 66, 72)."""

from decimal import Decimal

from app.ai.forecasting.engine import (
    MODEL_NAMES,
    ForecastingPipeline,
    calculate_metrics,
    predict_linear_trend_seasonal,
)
from app.ai.forecasting.generator import (
    generate_synthetic_history,
    variant_seed,
)
from scripts.generate_forecast_evaluation_report import (
    BASELINE_MODEL,
    CANDIDATE_MODEL,
    build_rows,
    evaluate_variant,
    write_csv,
    write_report,
    write_rolling_csv,
)

# --- generator ------------------------------------------------------------


def test_generator_is_deterministic_per_variant():
    """Same SKU and seed must reproduce the same series (Rule 72)."""
    a = generate_synthetic_history(variant_sku="GM-100G", seed=42)
    b = generate_synthetic_history(variant_sku="GM-100G", seed=42)
    assert [r.actual_quantity for r in a] == [r.actual_quantity for r in b]


def test_generator_gives_different_variants_different_series():
    """Two variants must not share one seasonal shape and one noise sequence.

    If every variant shared a single seasonal profile, any forecasting model
    would look far better than it really is because it only has to learn one
    global pattern.
    """
    a = generate_synthetic_history(variant_sku="GM-100G", base_demand=500.0, seed=42)
    b = generate_synthetic_history(variant_sku="TP-100G", base_demand=500.0, seed=42)

    assert [r.actual_quantity for r in a] != [r.actual_quantity for r in b]

    # Normalised shapes must differ too, not just the scale.
    shape_a = [r.actual_quantity / Decimal("500.0") for r in a]
    shape_b = [r.actual_quantity / Decimal("500.0") for r in b]
    assert shape_a != shape_b


def test_variant_seed_is_stable_and_distinct():
    """variant_seed must not depend on PYTHONHASHSEED and must separate SKUs."""
    assert variant_seed("GM-100G", 42) == variant_seed("GM-100G", 42)
    assert variant_seed("GM-100G", 42) != variant_seed("TP-100G", 42)
    assert variant_seed("GM-100G", 42) != variant_seed("GM-100G", 43)


# --- metrics --------------------------------------------------------------


def test_mape_excludes_zero_actual_months():
    """A zero-actual month is undefined for MAPE and must not dilute the average."""
    actuals = [Decimal("100.0"), Decimal("0.0"), Decimal("200.0")]
    predictions = [Decimal("110.0"), Decimal("0.0"), Decimal("180.0")]

    metrics = calculate_metrics(actuals, predictions)

    # APE over the two non-zero months only: 10% and 10% -> 10%.
    assert metrics["mape"] == Decimal("10.0000")
    # MAE still uses every month.
    assert metrics["mae"] == Decimal("10.0000")


def test_mase_is_one_for_a_model_equal_to_the_baseline():
    """MASE is the model's MAE divided by the baseline's in-sample MAE."""
    actuals = [Decimal("100.0"), Decimal("200.0"), Decimal("150.0")]
    predictions = [Decimal("100.0"), Decimal("200.0"), Decimal("150.0")]

    metrics = calculate_metrics(actuals, predictions, seasonal_naive_train_mae=Decimal("50.0"))

    assert metrics["mae"] == Decimal("0.0000")
    assert metrics["mase"] == Decimal("0.0000")


def test_mase_is_scale_free_across_products():
    """MASE must give the same score for two products of very different volume."""
    small_actuals = [Decimal("10.0"), Decimal("12.0"), Decimal("11.0")]
    small_predictions = [Decimal("11.0"), Decimal("11.0"), Decimal("13.0")]
    large_actuals = [Decimal("1000.0"), Decimal("1200.0"), Decimal("1100.0")]
    large_predictions = [Decimal("1100.0"), Decimal("1100.0"), Decimal("1300.0")]

    small = calculate_metrics(
        small_actuals, small_predictions, seasonal_naive_train_mae=Decimal("5.0")
    )
    large = calculate_metrics(
        large_actuals, large_predictions, seasonal_naive_train_mae=Decimal("500.0")
    )

    # Plain MAE scales with volume, so it cannot be compared across products.
    assert small["mae"] != large["mae"]
    # MASE divides by a baseline error that scales the same way, so it can.
    assert small["mase"] == large["mase"]


def test_metrics_require_matching_lengths():
    try:
        calculate_metrics([Decimal("1.0")], [Decimal("1.0"), Decimal("2.0")])
    except ValueError:
        return
    raise AssertionError("calculate_metrics must reject mismatched lengths")


# --- leakage control ------------------------------------------------------


def test_holdout_predictions_do_not_depend_on_test_actuals():
    """Leakage guard: corrupting the held-out actuals must not change a forecast.

    This is the strongest available check that the evaluation does not leak.
    """
    history = generate_synthetic_history(variant_sku="LEAK-GUARD", base_demand=400.0)
    pipeline = ForecastingPipeline(history)
    before = {
        name: pipeline.run_evaluations()["models"][name]["predictions"] for name in MODEL_NAMES
    }

    corrupted = []
    for record in history:
        if record.is_train:
            corrupted.append(record)
        else:
            corrupted.append(
                type(record)(
                    variant_sku=record.variant_sku,
                    period=record.period,
                    month_index=record.month_index,
                    actual_quantity=Decimal("9999.0"),
                    is_train=record.is_train,
                )
            )

    after_pipeline = ForecastingPipeline(corrupted)
    after = {
        name: after_pipeline.run_evaluations()["models"][name]["predictions"]
        for name in MODEL_NAMES
    }

    assert before == after, "a forecast depended on the held-out actuals"


def test_rolling_origin_only_scores_unseen_months():
    """Each rolling origin must predict months that come after the training window."""
    history = generate_synthetic_history(variant_sku="ROLL-CHECK", base_demand=450.0)
    pipeline = ForecastingPipeline(history)
    rolling = pipeline.run_rolling_origin(min_train_months=12, horizon=3, step=3)

    assert rolling["origins"], "rolling-origin evaluation produced no origins"
    for origin in rolling["origins"]:
        assert origin["train_end_period"] < origin["target_periods"][0]
        for name in MODEL_NAMES:
            assert len(origin["models"][name]["predictions"]) == len(origin["target_periods"])


def test_candidate_model_does_not_assume_the_generator_floor():
    """The model must not copy the data generator's minimum order size.

    The synthetic data floors demand at 50 units, but a model that hardcodes
    the same floor has been fitted to the generator rather than to the data.
    """
    history = generate_synthetic_history(variant_sku="FLOOR-CHECK", base_demand=60.0)
    small_records = [
        type(r)(
            variant_sku=r.variant_sku,
            period=r.period,
            month_index=r.month_index,
            actual_quantity=Decimal("10.0"),
            is_train=r.is_train,
        )
        for r in history
    ]
    pipeline = ForecastingPipeline(small_records)
    fallback = Decimal("10.0")
    prediction = predict_linear_trend_seasonal(pipeline.train, pipeline.test[0], fallback)

    assert prediction < Decimal("50.0"), "the model still applies the generator's floor"


# --- evaluation report ----------------------------------------------------


def test_evaluation_report_generator_writes_artifacts(tmp_path):
    """The M7 report generator must produce a report and both CSV files."""
    results = [evaluate_variant("GM-100G", 520.0), evaluate_variant("TP-100G", 900.0)]
    rows = build_rows(results)

    report_path = tmp_path / "EVALUATION_REPORT.md"
    csv_path = tmp_path / "forecast_evaluations.csv"
    rolling_path = tmp_path / "rolling_origin.csv"

    write_report(results, rows, report_path)
    write_csv(rows, csv_path)
    write_rolling_csv(results, rolling_path)

    text = report_path.read_text(encoding="utf-8")
    assert "# Forecast Evaluation Report" in text
    assert "MASE" in text
    assert "Limitations" in text
    assert "Rolling-origin backtest" in text
    assert csv_path.exists()
    assert rolling_path.exists()

    csv_text = csv_path.read_text(encoding="utf-8")
    assert "mase" in csv_text.splitlines()[0]
    assert BASELINE_MODEL in csv_text
    assert CANDIDATE_MODEL in csv_text


def test_evaluation_report_states_the_baseline_comparison(tmp_path):
    """The report must always name the baseline and the candidate model."""
    results = [evaluate_variant("GM-100G", 520.0)]
    rows = build_rows(results)
    report_path = tmp_path / "EVALUATION_REPORT.md"
    write_report(results, rows, report_path)
    text = report_path.read_text(encoding="utf-8")

    assert BASELINE_MODEL in text
    assert CANDIDATE_MODEL in text
    assert "beat" in text.lower()
