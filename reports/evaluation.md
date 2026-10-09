# FightIQ evaluation

Generated 2026-10-09 from 8,923 UFC bouts (1994-03-11 to 2026-10-03).

Every number below is **out of sample**: for each test year the model was trained only on bouts from earlier years, and every fighter stat was computed only from bouts before that fight date.

## Prediction quality, 2015–2026

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 5,770 | 61.9% | 0.6529 | 0.2305 |
| Your original 9 features, rebuilt point-in-time | 5,770 | 60.8% | 0.6586 | 0.2331 |
| Elo rating only | 5,770 | 55.5% | 0.6824 | 0.2447 |

Bouts with historical odds (4,508 bouts, years where the blend had at least one prior year to learn from):

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 4,508 | 62.1% | 0.6508 | 0.2295 |
| Bookmaker odds (margin removed) | 4,508 | 66.3% | 0.6068 | 0.2098 |
| **Blend: odds + FightIQ model** | 4,508 | 67.3% | 0.6042 | 0.2088 |

Lower log loss / Brier is better. The stats model alone is weaker than the betting market; the blend starts from the market price and adjusts it using the model.

## Calibration (favoured side)

**Stats model**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 2,881 | 54.9% | 55.4% |
| 60%-70% | 1,907 | 64.5% | 65.7% |
| 70%-80% | 804 | 74.1% | 70.9% |
| 80%-90% | 170 | 83.1% | 84.7% |
| 90%-100% | 8 | 90.8% | 75.0% |

**Blend**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 1,473 | 55.1% | 56.9% |
| 60%-70% | 1,506 | 65.1% | 64.5% |
| 70%-80% | 1,046 | 74.6% | 76.3% |
| 80%-90% | 439 | 84.0% | 87.9% |
| 90%-100% | 44 | 91.7% | 93.2% |

## Betting backtest (flat 1 unit, settled at the historical odds)

The rule used by `predict`: back a side when the blend gives it more than 5% expected value, and never bet a bout involving a UFC debutant (the model has no stats for them, and in the backtest it added nothing on those bouts).

| Strategy | Bets | Profit (units) | ROI |
|---|---|---|---|
| Stats model alone, EV > 0% | 3,229 | -139.1 | -4.3% |
| Stats model alone, EV > 5% | 2,779 | -143.9 | -5.2% |
| Stats model alone, EV > 10% | 2,428 | -150.6 | -6.2% |
| Stats model alone, EV > 20% | 1,770 | -135.4 | -7.6% |
| Blend, EV > 0% | 1,886 | +98.1 | +5.2% |
| Blend, EV > 5% | 599 | +84.0 | +14.0% |
| Blend, EV > 10% | 221 | +31.1 | +14.1% |
| Blend, EV > 20% | 49 | +13.9 | +28.5% |
| Blend, EV > 5%, debut bouts only (not bet) | 173 | -4.3 | -2.5% |
| Back every favourite | 3,628 | -124.3 | -3.4% |

Rule as used by `predict`: 95% interval for ROI (resampling whole cards) +4.4% to +24.1%.

By year:

| Year | Bets | Profit | ROI |
|---|---|---|---|
| 2016 | 164 | -1.0 | -0.6% |
| 2017 | 113 | +16.3 | +14.4% |
| 2018 | 62 | +9.0 | +14.5% |
| 2019 | 59 | +10.8 | +18.3% |
| 2020 | 34 | +15.0 | +44.3% |
| 2021 | 34 | +8.3 | +24.5% |
| 2022 | 51 | +5.7 | +11.1% |
| 2023 | 27 | +0.6 | +2.2% |
| 2024 | 24 | +11.5 | +47.7% |
| 2025 | 26 | +7.5 | +28.7% |
| 2026 | 5 | +0.4 | +8.5% |

By the odds of the side backed:

| Odds | Bets | ROI |
|---|---|---|
| 1.0–1.5 | 37 | +20.2% |
| 1.5–2.0 | 168 | +10.8% |
| 2.0–3.0 | 226 | +28.8% |
| 3.0–100.0 | 168 | -3.9% |

**Caveats.** The historical odds come from a public dataset built from bestfightodds.com and it is not documented whether they are opening, closing or best-available prices. If they are opening or best-across-books prices, real-world ROI will be lower. Treat this as evidence worth testing live, not as a guaranteed edge: log the odds you actually bet at and compare them with the closing line.

## Final model

Trained on 8,338 bouts (2005-02-05 to 2026-10-03). Coefficients are per unit of each A-minus-B feature; *impact* is the log-odds shift for a one-standard-deviation difference.

| Feature | Coefficient | Impact (1 sd) |
|---|---|---|
| age | -0.0548 | -0.287 |
| elo | +0.3915 | +0.243 |
| td_avg | +0.1389 | +0.182 |
| sapm | -0.1502 | -0.170 |
| exp_str_acc | +2.8955 | +0.157 |
| reach | +0.0457 | +0.151 |
| debut | -0.3334 | -0.146 |
| slpm | +0.0825 | +0.098 |
| ctrl_avg | -0.0388 | -0.081 |
| age_old | -0.0357 | -0.080 |
| experience | -0.0381 | -0.037 |
| layoff | -0.0381 | -0.031 |

Blend: logit(p) = 1.011 × logit(market) + 0.286 × model score.
