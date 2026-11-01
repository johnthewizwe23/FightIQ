# FightIQ evaluation

Generated 2026-11-01 from 8,962 UFC bouts (1994-03-11 to 2026-10-24).

Every number below is **out of sample**: for each test year the model was trained only on bouts from earlier years, and every fighter stat was computed only from bouts before that fight date.

## Prediction quality, 2015–2026

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 5,808 | 62.4% | 0.6440 | 0.2263 |
| Your original 9 features, rebuilt point-in-time | 5,808 | 60.7% | 0.6593 | 0.2334 |
| Elo rating only | 5,808 | 55.4% | 0.6824 | 0.2447 |

Bouts with historical odds (5,189 bouts, years where the blend had at least one prior year to learn from):

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 5,189 | 62.7% | 0.6416 | 0.2252 |
| Bookmaker odds (margin removed) | 5,189 | 66.8% | 0.6043 | 0.2088 |
| **Blend: odds + FightIQ model** | 5,189 | 67.6% | 0.6023 | 0.2078 |

Lower log loss / Brier is better. The stats model alone is weaker than the betting market; the blend starts from the market price and adjusts it using the model.

## Calibration (favoured side)

**Stats model**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 2,568 | 55.0% | 53.4% |
| 60%-70% | 1,917 | 64.6% | 65.3% |
| 70%-80% | 998 | 74.2% | 74.2% |
| 80%-90% | 309 | 83.6% | 80.6% |
| 90%-100% | 16 | 91.5% | 75.0% |

**Blend**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 1,673 | 55.0% | 57.4% |
| 60%-70% | 1,649 | 65.0% | 63.9% |
| 70%-80% | 1,204 | 74.6% | 76.4% |
| 80%-90% | 579 | 84.2% | 86.5% |
| 90%-100% | 84 | 92.1% | 90.5% |

## Betting backtest (flat 1 unit, settled at the historical odds)

The rule used by `predict`: back a side when the blend gives it more than 5% expected value, and never bet a bout involving a UFC debutant (the model has no stats for them, and in the backtest it added nothing on those bouts).

| Strategy | Bets | Profit (units) | ROI |
|---|---|---|---|
| Stats model alone, EV > 0% | 3,664 | -104.7 | -2.9% |
| Stats model alone, EV > 5% | 3,123 | -75.5 | -2.4% |
| Stats model alone, EV > 10% | 2,631 | -42.9 | -1.6% |
| Stats model alone, EV > 20% | 1,903 | -8.2 | -0.4% |
| Blend, EV > 0% | 2,569 | +161.1 | +6.3% |
| Blend, EV > 5% | 1,101 | +133.2 | +12.1% |
| Blend, EV > 10% | 472 | +102.3 | +21.7% |
| Blend, EV > 20% | 129 | +38.4 | +29.7% |
| Blend, EV > 5%, debut bouts only (not bet) | 295 | -33.8 | -11.4% |
| Back every favourite | 4,143 | -125.8 | -3.0% |

Rule as used by `predict`: 95% interval for ROI (resampling whole cards) +5.6% to +18.7%.

By year:

| Year | Bets | Profit | ROI |
|---|---|---|---|
| 2016 | 183 | +22.5 | +12.3% |
| 2017 | 174 | +20.3 | +11.7% |
| 2018 | 133 | +18.4 | +13.8% |
| 2019 | 123 | +14.3 | +11.6% |
| 2020 | 87 | +11.3 | +13.0% |
| 2021 | 99 | +8.4 | +8.5% |
| 2022 | 69 | +2.2 | +3.2% |
| 2023 | 61 | +16.6 | +27.2% |
| 2024 | 62 | +5.0 | +8.1% |
| 2025 | 46 | +16.5 | +35.8% |
| 2026 | 64 | -2.2 | -3.5% |

By the odds of the side backed:

| Odds | Bets | ROI |
|---|---|---|
| 1.0–1.5 | 144 | +7.5% |
| 1.5–2.0 | 333 | +7.3% |
| 2.0–3.0 | 411 | +21.1% |
| 3.0–100.0 | 213 | +5.3% |

**Caveats.** The historical odds are closing prices: for each fighter the middle of the closing range across bookmakers on bestfightodds.com (`odds_history/bestfightodds.csv`), then the odds FightIQ published when each card locked, then a public dataset for anything missing. Bets are settled at that middle price; the best price on the day is usually better, the price earlier in the week can be worse. Treat this as evidence worth testing live, not as a guaranteed edge: the Record page compares every suggested bet with the closing line.

## Final model

Trained on 8,376 bouts (2005-02-05 to 2026-10-24). Coefficients are per unit of each A-minus-B feature; *impact* is the log-odds shift for a one-standard-deviation difference.

| Feature | Coefficient | Impact (1 sd) |
|---|---|---|
| age | -0.0501 | -0.263 |
| glicko | +0.6595 | +0.262 |
| td_avg | +0.1961 | +0.257 |
| ctrl_avg | -0.1153 | -0.240 |
| adj_slpm | +0.2287 | +0.213 |
| glicko_rd | -2.1385 | -0.207 |
| adj_sapm | -0.2242 | -0.199 |
| debut | -0.4311 | -0.189 |
| experience | -0.1771 | -0.174 |
| reach | +0.0505 | +0.167 |
| pro_win_pct | +1.7117 | +0.161 |
| adj_ctrld | -0.1006 | -0.140 |
| slpm | -0.1084 | -0.129 |
| adj_ctrl | +0.0794 | +0.127 |
| exp_str_acc | +2.0240 | +0.110 |
| adj_td | -0.1048 | -0.107 |
| layoff | +0.0976 | +0.079 |
| age_old | -0.0296 | -0.066 |
| adj_acc | +0.8372 | +0.059 |
| sapm | -0.0252 | -0.029 |
| adj_tdd | +0.0268 | +0.023 |
| pro_years | +0.0035 | +0.015 |
| pro_fights | +0.0021 | +0.001 |

Blend: logit(p) = 0.889 × logit(market) + 0.314 × model score.
