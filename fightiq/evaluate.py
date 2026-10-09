"""Walk-forward evaluation: train on earlier years only, test on the next year."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .data import norm_name
from .model import FightModel

TRAIN_START = "2005-01-01"
TEST_YEARS = range(2015, 2027)
BET_THRESHOLDS = (0.0, 0.05, 0.10, 0.20)


def log_loss(y, p) -> float:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def devig(odds_a, odds_b):
    """Market probability for A with the bookmaker margin removed."""
    ia, ib = 1.0 / np.asarray(odds_a, float), 1.0 / np.asarray(odds_b, float)
    return ia / (ia + ib)


def attach_odds(X: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    """Join historical odds by date (+/- 1 day for time zones) and fighter names."""
    def pair_key(dates, a, b):
        a, b = np.asarray(a, object), np.asarray(b, object)
        lo, hi = np.where(a < b, a, b), np.where(a < b, b, a)
        return pd.Series(dates).dt.strftime("%Y-%m-%d").to_numpy() + "|" + lo + "|" + hi

    shifted = []
    for days in (0, -1, 1):
        o = odds.copy()
        o["date"] = o["date"] + pd.Timedelta(days=days)
        o["pk"] = pair_key(o["date"], o["key_r"], o["key_b"])
        o["shift"] = abs(days)
        shifted.append(o)
    o = pd.concat(shifted).sort_values("shift").drop_duplicates("pk")

    out = X.copy()
    ka, kb = out["name_a"].map(norm_name), out["name_b"].map(norm_name)
    out["pk"] = pair_key(out["date"], ka, kb)
    out = out.merge(o[["pk", "key_r", "odds_r", "odds_b"]].rename(columns={"odds_b": "odds_blue"}), on="pk", how="left")
    same = (out["key_r"] == ka.to_numpy()).to_numpy()
    out["odds_a"] = np.where(same, out["odds_r"], out["odds_blue"])
    out["odds_b"] = np.where(same, out["odds_blue"], out["odds_r"])
    out["p_market"] = devig(out["odds_a"], out["odds_b"])
    return out.drop(columns=["pk", "key_r", "odds_r", "odds_blue"])


def walk_forward(X: pd.DataFrame, features: list[str], C: float = 1.0,
                 years=TEST_YEARS, train_start: str = TRAIN_START) -> pd.DataFrame:
    """Out-of-sample predictions for each test year from a model fit on prior years."""
    dec = X[X["result_a"].isin([0.0, 1.0]) & (X["date"] >= train_start)]
    parts = []
    for year in years:
        train = dec[dec["date"] < f"{year}-01-01"]
        test = dec[dec["date"].dt.year == year].copy()
        if test.empty:
            continue
        m = FightModel(features, C).fit(train)
        test["model_score"] = m.score(test)
        test["p_model"] = 1 / (1 + np.exp(-test["model_score"]))
        test["year"] = year
        parts.append(test)
    return pd.concat(parts, ignore_index=True)


def fit_blend(rows: pd.DataFrame) -> np.ndarray:
    """Weights on [logit(market), model score]; symmetric, no intercept."""
    Z = np.column_stack([logit(rows["p_market"]), rows["model_score"]])
    y = rows["result_a"].to_numpy()
    lr = LogisticRegression(fit_intercept=False, C=1.0).fit(np.vstack([Z, -Z]), np.concatenate([y, 1 - y]))
    return lr.coef_[0]


def blend_proba(p_market, model_score, w) -> np.ndarray:
    z = w[0] * logit(p_market) + w[1] * np.asarray(model_score, float)
    return 1 / (1 + np.exp(-z))


def walk_forward_blend(P: pd.DataFrame, min_rows: int = 300) -> pd.DataFrame:
    """Blend weights for year Y are fit only on out-of-sample predictions from years < Y."""
    P = P.copy()
    P["p_blend"] = np.nan
    has = P["p_market"].notna()
    for year in sorted(P["year"].unique()):
        prior = P[has & (P["year"] < year)]
        cur = has & (P["year"] == year)
        if len(prior) < min_rows:
            continue
        w = fit_blend(prior)
        P.loc[cur, "p_blend"] = blend_proba(P.loc[cur, "p_market"], P.loc[cur, "model_score"], w)
    return P


def expected_value(p_a, odds_a, odds_b):
    """EV per unit staked for backing A and for backing B."""
    return p_a * odds_a - 1, (1 - p_a) * odds_b - 1


def betting_sim(P: pd.DataFrame, prob_col: str, threshold: float) -> pd.DataFrame:
    """Flat 1-unit bet on whichever side has EV above threshold, settled at the listed odds."""
    d = P.dropna(subset=[prob_col, "odds_a", "odds_b"])
    ev_a, ev_b = expected_value(d[prob_col], d["odds_a"], d["odds_b"])
    back_a = (ev_a > threshold) & (ev_a >= ev_b)
    back_b = (ev_b > threshold) & (ev_b > ev_a)
    pnl = (np.where(back_a, np.where(d["result_a"] == 1, d["odds_a"] - 1, -1.0), 0.0)
           + np.where(back_b, np.where(d["result_a"] == 0, d["odds_b"] - 1, -1.0), 0.0))
    return pd.DataFrame({"date": d["date"], "year": d["year"], "bet": back_a | back_b, "pnl": pnl,
                         "odds": np.where(back_a, d["odds_a"], np.where(back_b, d["odds_b"], np.nan))})


def scores(y, p) -> dict:
    y, p = np.asarray(y, float), np.asarray(p, float)
    return {"n": len(y), "accuracy": float(np.mean((p > 0.5) == (y == 1))),
            "log_loss": log_loss(y, p), "brier": float(np.mean((p - y) ** 2))}


def calibration(y, p, bins=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0)) -> pd.DataFrame:
    """Calibration for the favoured side of each bout."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    fav_p = np.maximum(p, 1 - p)
    fav_won = np.where(p >= 0.5, y, 1 - y)
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (fav_p >= lo) & (fav_p < hi)
        if m.any():
            rows.append({"predicted": f"{lo:.0%}-{hi:.0%}", "fights": int(m.sum()),
                         "avg_predicted": float(fav_p[m].mean()), "actual_win_rate": float(fav_won[m].mean())})
    return pd.DataFrame(rows)


def event_bootstrap_roi(sim: pd.DataFrame, n: int = 5000, seed: int = 0) -> tuple[float, float]:
    """95% interval for ROI, resampling whole event days (bets on one card are correlated)."""
    b = sim[sim["bet"]]
    days = b.groupby("date")["pnl"].agg(["sum", "size"])
    rng = np.random.default_rng(seed)
    idx = np.arange(len(days))
    s, c = days["sum"].to_numpy(), days["size"].to_numpy()
    draws = [s[k].sum() / c[k].sum() for k in (rng.choice(idx, len(idx)) for _ in range(n))]
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))
