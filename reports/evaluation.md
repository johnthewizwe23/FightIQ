# FightIQ evaluation

Generated 2026-10-12 from 8,935 UFC bouts (1994-03-11 to 2026-10-10).

Every number below is **out of sample**: for each test year the model was trained only on bouts from earlier years, and every fighter stat was computed only from bouts before that fight date.

## Prediction quality, 2015–2026

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 5,781 | 61.8% | 0.6532 | 0.2306 |
| Your original 9 features, rebuilt point-in-time | 5,781 | 60.8% | 0.6589 | 0.2332 |
| Elo rating only | 5,781 | 55.4% | 0.6824 | 0.2447 |

Bouts with historical odds (4,484 bouts, years where the blend had at least one prior year to learn from):

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 4,484 | 62.2% | 0.6504 | 0.2293 |
| Bookmaker odds (margin removed) | 4,484 | 66.3% | 0.6053 | 0.2093 |
| **Blend: odds + FightIQ model** | 4,484 | 67.4% | 0.6038 | 0.2086 |

Lower log loss / Brier is better. The stats model alone is weaker than the betting market; the blend starts from the market price and adjusts it using the model.

## Calibration (favoured side)

**Stats model**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 2,886 | 54.9% | 55.4% |
| 60%-70% | 1,910 | 64.5% | 65.6% |
| 70%-80% | 807 | 74.1% | 70.8% |
| 80%-90% | 170 | 83.1% | 84.7% |
| 90%-100% | 8 | 90.8% | 75.0% |

**Blend**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 1,477 | 55.1% | 57.3% |
| 60%-70% | 1,481 | 65.0% | 64.6% |
| 70%-80% | 1,032 | 74.5% | 75.9% |
| 80%-90% | 437 | 84.1% | 88.1% |
| 90%-100% | 57 | 92.2% | 91.2% |

## Betting backtest (flat 1 unit, settled at the historical odds)

The rule used by `predict`: back a side when the blend gives it more than 5% expected value, and never bet a bout involving a UFC debutant (the model has no stats for them, and in the backtest it added nothing on those bouts).

| Strategy | Bets | Profit (units) | ROI |
|---|---|---|---|
| Stats model alone, EV > 0% | 3,222 | -141.3 | -4.4% |
| Stats model alone, EV > 5% | 2,774 | -146.4 | -5.3% |
| Stats model alone, EV > 10% | 2,423 | -153.1 | -6.3% |
| Stats model alone, EV > 20% | 1,765 | -137.9 | -7.8% |
| Blend, EV > 0% | 1,900 | +116.5 | +6.1% |
| Blend, EV > 5% | 584 | +95.5 | +16.3% |
| Blend, EV > 10% | 212 | +22.3 | +10.5% |
| Blend, EV > 20% | 46 | +12.6 | +27.5% |
| Blend, EV > 5%, debut bouts only (not bet) | 161 | -7.7 | -4.8% |
| Back every favourite | 3,616 | -123.8 | -3.4% |

Rule as used by `predict`: 95% interval for ROI (resampling whole cards) +6.9% to +26.3%.

By year:

| Year | Bets | Profit | ROI |
|---|---|---|---|
| 2016 | 162 | +6.1 | +3.8% |
| 2017 | 111 | +17.4 | +15.6% |
| 2018 | 58 | +9.3 | +16.0% |
| 2019 | 60 | +12.3 | +20.5% |
| 2020 | 34 | +15.2 | +44.7% |
| 2021 | 33 | +9.3 | +28.3% |
| 2022 | 47 | +2.8 | +6.0% |
| 2023 | 25 | +1.1 | +4.3% |
| 2024 | 23 | +14.0 | +60.9% |
| 2025 | 25 | +8.5 | +33.9% |
| 2026 | 6 | -0.6 | -9.6% |

By the odds of the side backed:

| Odds | Bets | ROI |
|---|---|---|
| 1.0–1.5 | 36 | +19.9% |
| 1.5–2.0 | 162 | +13.9% |
| 2.0–3.0 | 230 | +29.7% |
| 3.0–100.0 | 156 | -1.5% |

**Caveats.** The historical odds come from a public dataset built from bestfightodds.com and it is not documented whether they are opening, closing or best-available prices. If they are opening or best-across-books prices, real-world ROI will be lower. Treat this as evidence worth testing live, not as a guaranteed edge: log the odds you actually bet at and compare them with the closing line.

## Final model

Trained on 8,349 bouts (2005-02-05 to 2026-10-10). Coefficients are per unit of each A-minus-B feature; *impact* is the log-odds shift for a one-standard-deviation difference.

| Feature | Coefficient | Impact (1 sd) |
|---|---|---|
| age | -0.0551 | -0.289 |
| elo | +0.3884 | +0.241 |
| td_avg | +0.1381 | +0.181 |
| sapm | -0.1497 | -0.170 |
| exp_str_acc | +2.8484 | +0.155 |
| reach | +0.0462 | +0.152 |
| debut | -0.3296 | -0.144 |
| slpm | +0.0831 | +0.099 |
| ctrl_avg | -0.0376 | -0.078 |
| age_old | -0.0347 | -0.078 |
| experience | -0.0342 | -0.034 |
| layoff | -0.0362 | -0.029 |

Blend: logit(p) = 0.937 × logit(market) + 0.287 × model score.
