import json

import numpy as np
import pandas as pd

from fightiq import evaluate as ev
from fightiq import features, history


def synthetic_history(seed=0):
    """A few small early years, then big years with odds so the blend and bet rule kick in."""
    rng = np.random.default_rng(seed)
    rows = []
    for year in range(2003, 2018):
        n = 400 if year >= 2015 else 60
        for i in range(n):
            f = {"f_" + c: rng.normal() for c in features.FEATURES_FULL}
            p = 1 / (1 + np.exp(-(0.8 * f["f_elo"] + 0.4 * f["f_slpm"])))
            result = float(rng.random() < p)
            if i == 0:
                result = 0.5
            elif i == 1:
                result = np.nan
            market = np.clip(p + rng.normal(0, 0.08), 0.05, 0.95)
            rows.append({"fight_id": f"{year}-{i}", "date": pd.Timestamp(f"{year}-{1 + i % 12:02d}-15"),
                         "event": f"UFC {year}-{i % 12}", "bout_seq": i, "name_a": f"Fighter {year} {i} A",
                         "name_b": f"Fighter {year} {i} B", "result_a": result, "method": "KO/TKO",
                         "division": "lightweight", "women": False, "title": i == 2,
                         "ufc_fights_a": int(rng.integers(0, 8)), "ufc_fights_b": int(rng.integers(1, 8)),
                         "odds_a": round(0.95 / market, 2) if year >= 2010 else np.nan,
                         "odds_b": round(0.95 / (1 - market), 2) if year >= 2010 else np.nan, **f})
    X = pd.DataFrame(rows).sort_values(["date", "bout_seq"]).reset_index(drop=True)
    X["p_market"] = ev.devig(X["odds_a"], X["odds_b"])
    return X


def test_every_fight_gets_a_walk_forward_pick_except_the_first_year():
    X = synthetic_history()
    P = history.predictions(X)
    assert len(P) == len(X) and set(P["fight_id"]) == set(X["fight_id"])
    first = P["date"].dt.year == 2003
    assert P.loc[first, "p_model"].isna().all() and P.loc[~first, "p_model"].notna().all()
    # draws and no contests are scored too, but never blended or bet
    undecided = ~P["result_a"].isin([0.0, 1.0])
    assert P.loc[undecided & ~first, "p_model"].notna().all()
    assert P.loc[undecided, "p_blend"].isna().all() and P.loc[undecided, "bet"].isna().all()
    # the blend starts once a full backtest year is available to fit it on
    assert P.loc[P["date"].dt.year < 2016, "p_blend"].isna().all()
    assert P.loc[(P["date"].dt.year >= 2016) & ~undecided, "p_blend"].notna().all()


def test_matches_the_backtest_exactly():
    X = synthetic_history()
    P = history.predictions(X)
    bt = ev.walk_forward_blend(ev.walk_forward(X, features.FEATURES_FULL, 1.0))
    merged = bt.merge(P[["fight_id", "p_model", "p_blend", "bet"]], on="fight_id", suffixes=("", "_h"))
    assert len(merged) == len(bt)
    assert np.allclose(merged["p_model"], merged["p_model_h"])
    assert np.allclose(merged["p_blend"], merged["p_blend_h"], equal_nan=True)
    no_debut = bt[bt["p_blend"].notna() & (bt["ufc_fights_a"] > 0) & (bt["ufc_fights_b"] > 0)]
    sim = ev.betting_sim(no_debut, "p_blend", history.BET_THRESHOLD)
    assert P["bet"].notna().sum() == sim["bet"].sum() > 0
    assert not P.loc[P["bet"].notna(), "ufc_fights_a"].eq(0).any()


def test_json_is_compact_and_round_trips():
    X = synthetic_history()
    P = history.predictions(X)
    text = history.to_json(P)
    d = json.loads(text)
    assert len(d["fights"]) == len(P) and d["columns"] == history.COLUMNS
    assert d["fights_to"] == "2017-12-15"
    first = d["fights"][0]
    assert d["events"][first[0]][1] == P["event"].iloc[0] and first[4] is None
    results = {r[3] for r in d["fights"]}
    assert results == {0, 1, 0.5, None}
    assert "Lightweight title" in d["divisions"] and d["methods"] == ["KO/TKO"]
    assert sum(1 for line in text.splitlines() if line.startswith("[")) == len(P) + len(d["events"])
