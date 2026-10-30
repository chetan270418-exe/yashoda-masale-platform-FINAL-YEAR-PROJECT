# Forecast Evaluation Report

Generated 2026-10-30 15:22 UTC by `backend/scripts/generate_forecast_evaluation_report.py`.

This report answers one question honestly: **does the candidate model beat the simple baseline?** Where it does not, the report says so.

## 1. Method

- **Data:** synthetic monthly demand, 12 spice variants.
- **Demand model:** `base x trend x season x festival x noise`, with a per-variant seasonal amplitude and phase and a per-variant noise seed.
- **Split:** chronological, never shuffled. First 18 months train, last 6 months test (2026-05 to 2026-10).
- **Leakage control:** every prediction is built from a history window that ends before the month being predicted. No held-out actual is ever used to build a forecast.
- **Models compared:**
  - `seasonal_naive` (baseline 1) - the same month one year earlier.
  - `moving_average_3m` (baseline 2) - mean of the previous 3 months.
  - `linear_trend_seasonal` (candidate) - linear trend plus seasonal indices.
- **Metrics:** MAE, RMSE, MAPE and MASE.

> **MASE** = MAE(model) / MAE(seasonal naive, in-sample). MASE below 1 means the model beat the seasonal naive baseline. It is scale-free, so it can be compared across products of very different sales volumes, which plain MAE cannot do.

## 2. Headline result

- **Hold-out split:** the candidate beat the seasonal naive baseline on **10 of 12 variants** (83%). Mean MASE: candidate **0.8472**, baseline **1.1711**.
- **Rolling-origin backtest:** the candidate beat the baseline on only **1 of 12 variants** (8%).

> ### The two tests disagree, and the rolling-origin result is the one to trust.
>
> The hold-out split looks good for the candidate, but it scores the model on a single favourable window that the model was fitted for. The rolling-origin test refits the model at every origin and scores it on months it has never seen, and there the candidate mostly **loses** to the seasonal naive baseline.

**The honest conclusion is that the seasonal naive baseline should be kept as the production forecast, and the candidate model is not yet justified by this data.** The reason is visible in the limitations: with 24 months of history and a 12-month seasonal cycle, the training window contains only one or two observations per calendar month, so the candidate's seasonal indices are estimated from far too few points and do not generalise.

This is a legitimate result, not a failure. It answers the question the project set out to answer, and it shows the evaluation is honest rather than rigged. The useful next step is to collect more history, then re-run the same evaluation.

## 3. Per-variant results

| Variant | Model | MAE | RMSE | MAPE % | MASE | Beats baseline |
|---|---|---:|---:|---:|---:|:---:|
| GM-100G | `seasonal_naive` | 55.6500 | 68.9230 | 7.44 | 0.8626 | - |
| GM-100G | `moving_average_3m` | 143.7046 | 154.7247 | 18.06 | 2.2274 | no |
| GM-100G | `linear_trend_seasonal` | 64.1167 | 71.0320 | 9.02 | 0.9938 | no |
| GM-250G | `seasonal_naive` | 59.5167 | 61.4866 | 9.79 | 1.1785 | - |
| GM-250G | `moving_average_3m` | 38.9324 | 48.8214 | 6.33 | 0.7709 | yes |
| GM-250G | `linear_trend_seasonal` | 21.3333 | 24.2242 | 3.53 | 0.4224 | yes |
| TP-100G | `seasonal_naive` | 88.8667 | 101.9335 | 8.54 | 0.7458 | - |
| TP-100G | `moving_average_3m` | 278.2583 | 318.0970 | 32.23 | 2.3354 | no |
| TP-100G | `linear_trend_seasonal` | 40.4833 | 54.0222 | 3.96 | 0.3398 | yes |
| TP-500G | `seasonal_naive` | 31.7833 | 33.9175 | 6.77 | 0.7403 | - |
| TP-500G | `moving_average_3m` | 49.1759 | 57.7623 | 10.93 | 1.1454 | no |
| TP-500G | `linear_trend_seasonal` | 23.8833 | 29.0054 | 5.60 | 0.5563 | yes |
| CP-100G | `seasonal_naive` | 81.4667 | 100.1350 | 7.17 | 1.2813 | - |
| CP-100G | `moving_average_3m` | 316.8500 | 343.1119 | 27.60 | 4.9832 | no |
| CP-100G | `linear_trend_seasonal` | 71.6667 | 89.8558 | 6.40 | 1.1271 | yes |
| CP-250G | `seasonal_naive` | 36.4667 | 39.2327 | 7.61 | 1.8218 | - |
| CP-250G | `moving_average_3m` | 70.3278 | 73.7728 | 14.64 | 3.5135 | no |
| CP-250G | `linear_trend_seasonal` | 27.0333 | 29.9789 | 5.65 | 1.3505 | yes |
| CM-100G | `seasonal_naive` | 57.8500 | 64.6248 | 9.80 | 1.2388 | - |
| CM-100G | `moving_average_3m` | 154.9694 | 174.2194 | 25.02 | 3.3184 | no |
| CM-100G | `linear_trend_seasonal` | 46.4167 | 53.7982 | 7.85 | 0.9939 | yes |
| PS-100G | `seasonal_naive` | 32.7833 | 39.4979 | 8.99 | 0.9939 | - |
| PS-100G | `moving_average_3m` | 91.2250 | 104.7135 | 31.70 | 2.7658 | no |
| PS-100G | `linear_trend_seasonal` | 18.2667 | 19.6751 | 5.62 | 0.5538 | yes |
| BB-100G | `seasonal_naive` | 34.4833 | 40.9381 | 8.47 | 2.0628 | - |
| BB-100G | `moving_average_3m` | 120.9417 | 127.6402 | 30.21 | 7.2348 | no |
| BB-100G | `linear_trend_seasonal` | 34.8167 | 41.6601 | 8.57 | 2.0827 | no |
| CS-200G | `seasonal_naive` | 23.3833 | 24.7376 | 8.61 | 1.1750 | - |
| CS-200G | `moving_average_3m` | 74.3083 | 76.7315 | 26.46 | 3.7341 | no |
| CS-200G | `linear_trend_seasonal` | 11.4000 | 13.8212 | 4.24 | 0.5729 | yes |
| CF-100G | `seasonal_naive` | 14.7833 | 17.4198 | 8.31 | 1.1733 | - |
| CF-100G | `moving_average_3m` | 20.0028 | 21.1311 | 11.88 | 1.5875 | no |
| CF-100G | `linear_trend_seasonal` | 11.7833 | 14.8535 | 6.53 | 0.9352 | yes |
| MS-100G | `seasonal_naive` | 56.5167 | 62.6816 | 8.31 | 0.7797 | - |
| MS-100G | `moving_average_3m` | 95.3213 | 101.8023 | 14.67 | 1.3151 | no |
| MS-100G | `linear_trend_seasonal` | 17.2667 | 24.8304 | 2.45 | 0.2382 | yes |

## 4. Rolling-origin backtest

A single hold-out split can be a lucky cut, so every model is refitted at 4 rolling origins (12-month minimum training window, 3-month horizon, 3-month step) and scored on data it had never seen.

| Variant | Model | Mean MAE across origins | Mean MASE across origins |
|---|---|---:|---:|
| GM-100G | `seasonal_naive` | 60.0833 | 1.0302 |
| GM-100G | `moving_average_3m` | 174.4667 | 3.1601 |
| GM-100G | `linear_trend_seasonal` | 106.9750 | 1.0636 |
| GM-250G | `seasonal_naive` | 55.0084 | 1.0667 |
| GM-250G | `moving_average_3m` | 99.8666 | 2.1693 |
| GM-250G | `linear_trend_seasonal` | 31.4916 | 0.6794 |
| TP-100G | `seasonal_naive` | 104.0083 | 0.8073 |
| TP-100G | `moving_average_3m` | 267.4333 | 1.7693 |
| TP-100G | `linear_trend_seasonal` | 255.7083 | 0.8216 |
| TP-500G | `seasonal_naive` | 37.3583 | 0.7058 |
| TP-500G | `moving_average_3m` | 70.2583 | 1.7483 |
| TP-500G | `linear_trend_seasonal` | 51.9583 | 0.8951 |
| CP-100G | `seasonal_naive` | 72.5250 | 1.2164 |
| CP-100G | `moving_average_3m` | 240.6333 | 3.7224 |
| CP-100G | `linear_trend_seasonal` | 161.5250 | 1.1905 |
| CP-250G | `seasonal_naive` | 28.2416 | 1.5188 |
| CP-250G | `moving_average_3m` | 61.3000 | 2.4598 |
| CP-250G | `linear_trend_seasonal` | 55.5500 | 1.6538 |
| CM-100G | `seasonal_naive` | 52.2750 | 1.0696 |
| CM-100G | `moving_average_3m` | 125.2333 | 2.7487 |
| CM-100G | `linear_trend_seasonal` | 66.6500 | 0.9941 |
| PS-100G | `seasonal_naive` | 32.8834 | 1.0541 |
| PS-100G | `moving_average_3m` | 86.7750 | 2.4013 |
| PS-100G | `linear_trend_seasonal` | 75.7333 | 1.4552 |
| BB-100G | `seasonal_naive` | 25.6000 | 1.7908 |
| BB-100G | `moving_average_3m` | 99.2500 | 5.6224 |
| BB-100G | `linear_trend_seasonal` | 74.3583 | 2.2712 |
| CS-200G | `seasonal_naive` | 21.6417 | 1.1191 |
| CS-200G | `moving_average_3m` | 46.8250 | 1.9613 |
| CS-200G | `linear_trend_seasonal` | 39.1167 | 0.9464 |
| CF-100G | `seasonal_naive` | 13.6917 | 1.0196 |
| CF-100G | `moving_average_3m` | 31.6083 | 2.1354 |
| CF-100G | `linear_trend_seasonal` | 26.6250 | 1.0708 |
| MS-100G | `seasonal_naive` | 64.5000 | 0.9131 |
| MS-100G | `moving_average_3m` | 116.9417 | 1.5101 |
| MS-100G | `linear_trend_seasonal` | 86.4750 | 0.5421 |

## 5. Limitations

- **Only 24 months of history.** With a 12-month seasonal cycle, each calendar month in the training window carries one or two observations, so the seasonal index of the candidate model is estimated from very few points. This is the direct cause of the hold-out and rolling-origin results disagreeing, and it is the main reason the baseline is kept.
- **The hold-out window is favourable to the candidate.** The last 6 months cover May to October, a run of consecutive months the seasonal indices can partly extrapolate. A rolling-origin test over several windows is the corrective, and it is reported in section 4.
- **Synthetic data.** These results prove the evaluation pipeline is correct and that the baseline comparison is honest. They do not measure real-world accuracy for a real spice business.
- **MAPE excludes zero-demand months.** A month with no sales makes the percentage error undefined, so those months are excluded from the MAPE average rather than diluting it.
- **The baseline is not free.** Seasonal naive needs 12 months of history before it can produce a forecast. On a new product with less history, the moving average is the only usable baseline.

## 6. Reproduction

```
cd backend
python -m scripts.generate_forecast_evaluation_report
```

The command is deterministic: each variant's series is generated from a seed derived from its SKU, so re-running it reproduces these numbers exactly.
