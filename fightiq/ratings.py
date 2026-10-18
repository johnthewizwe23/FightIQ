"""Glicko ratings: results weighted by quality of opposition, with a measure of how sure each rating is.

Glicko-1, one rating period per fight day: every update from a day is applied after all of that day's bouts have
been read, so a bout only sees results from strictly earlier dates (the same rule as every other feature).
A rating's uncertainty (RD) grows with time out of the cage and shrinks with each fight. Finishes move ratings by
the full amount, split and majority decisions by half. Upcoming bouts (no result) get pre-fight ratings and change
nothing.

In the walk-forward backtest this replaced Elo: the stats model's log loss improved by about 0.005 on both
2015-2020 and 2021-2026 (paired card-level bootstrap, better in more than 99% of resamples).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

START = 1500.0
RD_START = 150.0   # uncertainty of a newcomer's rating (also the ceiling after long layoffs)
RD_MIN = 30.0
C = 60.0           # RD growth per year out: RD^2 + C^2 * years
FINISH = {"KO/TKO", "Submission", "TKO - Doctor's Stoppage"}
CLOSE = {"Decision - Split", "Decision - Majority"}
CLOSE_WEIGHT = 0.5
Q = math.log(10.0) / 400.0


def _g(rd: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * Q * Q * rd * rd / (math.pi ** 2))


def glicko(fights: pd.DataFrame) -> pd.DataFrame:
    """Pre-fight rating and RD of both corners for every bout, plus `glicko_lg`: the ratings' log-odds that A wins."""
    f = fights.sort_values(["date"], kind="stable").reset_index(drop=True)
    method = f["method"].fillna("").to_numpy()
    mult = np.where(np.isin(method, list(CLOSE)), CLOSE_WEIGHT, 1.0)
    ids = pd.unique(pd.concat([f["id_a"], f["id_b"]]))
    idx = {k: i for i, k in enumerate(ids)}
    ia, ib = f["id_a"].map(idx).to_numpy(), f["id_b"].map(idx).to_numpy()
    r = np.full(len(ids), np.nan)
    rd = np.full(len(ids), RD_START)
    last = np.full(len(ids), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    res = f["result_a"].to_numpy(dtype=float)
    out = np.zeros((len(f), 5))
    for day, rows in sorted(f.groupby("date").indices.items()):
        day64 = np.datetime64(day, "ns")
        seen = set()
        for i in rows:
            for p in (ia[i], ib[i]):
                if p in seen:
                    continue
                seen.add(p)
                if np.isnan(r[p]):
                    r[p], rd[p] = START, RD_START
                else:
                    years = (day64 - last[p]) / np.timedelta64(1, "D") / 365.25
                    rd[p] = min(math.sqrt(rd[p] ** 2 + C * C * years), RD_START)
        acc: dict[int, list] = {}
        for i in rows:
            a, b, s = ia[i], ib[i], res[i]
            lg = Q * _g(math.sqrt(rd[a] ** 2 + rd[b] ** 2)) * (r[a] - r[b])
            out[i] = (r[a], r[b], rd[a], rd[b], lg)
            if s == s:  # no contests and upcoming bouts change nothing
                for p, o, sp in ((a, b, s), (b, a, 1 - s)):
                    gj = _g(rd[o])
                    e = 1.0 / (1.0 + 10 ** (-gj * (r[p] - r[o]) / 400.0))
                    t = acc.setdefault(p, [0.0, 0.0])
                    t[0] += gj * gj * e * (1 - e)
                    t[1] += gj * (sp - e) * mult[i]
        for p, (dsum, ssum) in acc.items():
            denom = 1.0 / rd[p] ** 2 + Q * Q * dsum
            r[p] += Q / denom * ssum
            rd[p] = max(math.sqrt(1.0 / denom), RD_MIN)
        for i in rows:
            last[ia[i]] = last[ib[i]] = day64
    return pd.DataFrame({"fight_id": f["fight_id"], "glicko_a": out[:, 0], "glicko_b": out[:, 1],
                         "glicko_rd_a": out[:, 2], "glicko_rd_b": out[:, 3], "glicko_lg": out[:, 4]})
