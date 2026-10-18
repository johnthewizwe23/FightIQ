# FightIQ evaluation

Generated 2026-10-18 from 8,935 UFC bouts (1994-03-11 to 2026-10-10).

Every number below is **out of sample**: for each test year the model was trained only on bouts from earlier years, and every fighter stat was computed only from bouts before that fight date.

## Prediction quality, 2015–2026

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 5,781 | 62.5% | 0.6437 | 0.2261 |
| Your original 9 features, rebuilt point-in-time | 5,781 | 60.8% | 0.6589 | 0.2332 |
| Elo rating only | 5,781 | 55.4% | 0.6824 | 0.2447 |

Bouts with historical odds (5,163 bouts, years where the blend had at least one prior year to learn from):

| Model | Bouts | Accuracy | Log loss | Brier |
|---|---|---|---|---|
| FightIQ model (stats only) | 5,163 | 62.7% | 0.6413 | 0.2250 |
| Bookmaker odds (margin removed) | 5,163 | 66.8% | 0.6037 | 0.2086 |
| **Blend: odds + FightIQ model** | 5,163 | 67.7% | 0.6017 | 0.2076 |

Lower log loss / Brier is better. The stats model alone is weaker than the betting market; the blend starts from the market price and adjusts it using the model.

## Calibration (favoured side)

**Stats model**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 2,555 | 55.0% | 53.4% |
| 60%-70% | 1,909 | 64.6% | 65.4% |
| 70%-80% | 993 | 74.2% | 74.3% |
| 80%-90% | 308 | 83.6% | 80.5% |
| 90%-100% | 16 | 91.5% | 75.0% |

**Blend**

| Predicted | Bouts | Avg predicted | Actually won |
|---|---|---|---|
| 50%-60% | 1,659 | 55.0% | 57.3% |
| 60%-70% | 1,645 | 65.0% | 63.9% |
| 70%-80% | 1,201 | 74.5% | 76.4% |
| 80%-90% | 574 | 84.2% | 86.8% |
| 90%-100% | 84 | 92.1% | 90.5% |

## Betting backtest (flat 1 unit, settled at the historical odds)

The rule used by `predict`: back a side when the blend gives it more than 5% expected value, and never bet a bout involving a UFC debutant (the model has no stats for them, and in the backtest it added nothing on those bouts).

| Strategy | Bets | Profit (units) | ROI |
|---|---|---|---|
| Stats model alone, EV > 0% | 3,645 | -101.2 | -2.8% |
| Stats model alone, EV > 5% | 3,110 | -74.0 | -2.4% |
| Stats model alone, EV > 10% | 2,618 | -40.0 | -1.5% |
| Stats model alone, EV > 20% | 1,890 | -5.3 | -0.3% |
| Blend, EV > 0% | 2,551 | +165.2 | +6.5% |
| Blend, EV > 5% | 1,090 | +138.6 | +12.7% |
| Blend, EV > 10% | 467 | +101.0 | +21.6% |
| Blend, EV > 20% | 128 | +37.7 | +29.5% |
| Blend, EV > 5%, debut bouts only (not bet) | 292 | -41.6 | -14.3% |
| Back every favourite | 4,123 | -127.6 | -3.1% |

Rule as used by `predict`: 95% interval for ROI (resampling whole cards) +6.4% to +19.4%.

By year:

| Year | Bets | Profit | ROI |
|---|---|---|---|
| 2016 | 183 | +22.5 | +12.3% |
| 2017 | 174 | +20.3 | +11.7% |
| 2018 | 133 | +18.4 | +13.8% |
| 2019 | 123 | +14.3 | +11.6% |
| 2020 | 87 | +11.3 | +13.0% |
| 2021 | 98 | +9.4 | +9.6% |
| 2022 | 69 | +2.2 | +3.2% |
| 2023 | 61 | +16.6 | +27.2% |
| 2024 | 62 | +5.0 | +8.1% |
| 2025 | 47 | +17.4 | +37.1% |
| 2026 | 53 | +1.2 | +2.3% |

By the odds of the side backed:

| Odds | Bets | ROI |
|---|---|---|
| 1.0–1.5 | 141 | +8.9% |
| 1.5–2.0 | 329 | +8.2% |
| 2.0–3.0 | 407 | +21.6% |
| 3.0–100.0 | 213 | +5.3% |

**Caveats.** The historical odds are closing prices: for each fighter the middle of the closing range across bookmakers on bestfightodds.com (`odds_history/bestfightodds.csv`), then the odds FightIQ published when each card locked, then a public dataset for anything missing. Bets are settled at that middle price; the best price on the day is usually better, the price earlier in the week can be worse. Treat this as evidence worth testing live, not as a guaranteed edge: the Record page compares every suggested bet with the closing line.

## Final model

Trained on 8,349 bouts (2005-02-05 to 2026-10-10). Coefficients are per unit of each A-minus-B feature; *impact* is the log-odds shift for a one-standard-deviation difference.

| Feature | Coefficient | Impact (1 sd) |
|---|---|---|
| glicko | +0.6602 | +0.262 |
| age | -0.0498 | -0.261 |
| td_avg | +0.1975 | +0.258 |
| ctrl_avg | -0.1142 | -0.238 |
| adj_slpm | +0.2359 | +0.220 |
| glicko_rd | -2.1339 | -0.207 |
| adj_sapm | -0.2244 | -0.200 |
| debut | -0.4305 | -0.189 |
| experience | -0.1794 | -0.176 |
| reach | +0.0503 | +0.166 |
| pro_win_pct | +1.7108 | +0.161 |
| adj_ctrld | -0.0985 | -0.137 |
| slpm | -0.1124 | -0.133 |
| adj_ctrl | +0.0781 | +0.125 |
| exp_str_acc | +2.0522 | +0.111 |
| adj_td | -0.1052 | -0.108 |
| layoff | +0.0965 | +0.078 |
| age_old | -0.0306 | -0.068 |
| adj_acc | +0.8092 | +0.057 |
| sapm | -0.0264 | -0.030 |
| adj_tdd | +0.0255 | +0.022 |
| pro_years | +0.0031 | +0.013 |
| pro_fights | +0.0029 | +0.002 |

Blend: logit(p) = 0.892 × logit(market) + 0.314 × model score.
