import numpy as np
import pandas as pd
import pytest

from fightiq import features
from fightiq.model import FightModel


def make_fights(rows):
    """rows: (date, a, b, result_a, seconds, a_stats, b_stats) with stats = (sig_l, sig_a, td_l, td_a)."""
    fights, rounds = [], []
    for i, (date, a, b, res, secs, sa, sb) in enumerate(rows):
        fid = f"f{i}"
        fights.append({"fight_id": fid, "event": f"E{i}", "bout": f"{a} vs. {b}", "date": pd.Timestamp(date),
                       "name_a": a, "name_b": b, "id_a": a, "id_b": b, "result_a": res, "method": "Decision",
                       "division": "lightweight", "women": False, "title": False, "scheduled_rounds": 3.0,
                       "seconds": float(secs)})
        for side, st in (("a", sa), ("b", sb)):
            rounds.append({"fight_id": fid, "side": side, "sig_l": st[0], "sig_a": st[1], "td_l": st[2],
                           "td_a": st[3], "head_l": 0.0, "kd": 0.0, "sub_att": 0.0, "ctrl": 0.0})
    fighters = pd.DataFrame({"fighter_id": list("XYZ"), "name": list("XYZ"), "height": 70.0, "reach": 72.0,
                             "weight": 155.0, "stance": "Orthodox", "dob": pd.Timestamp("1990-01-01"), "key": list("xyz")})
    return pd.DataFrame(fights), pd.DataFrame(rounds), fighters


PRIORS = {"slpm": 4.0, "str_acc": 0.45, "td_avg": 1.5, "td_acc": 0.4, "kd_avg": 0.2, "sub_avg": 0.5, "ctrl_avg": 2.0}

ROWS = [
    ("2020-01-01", "X", "Y", 1.0, 900, (60, 100, 2, 4), (30, 100, 0, 3)),
    ("2020-06-01", "X", "Z", 0.0, 300, (10, 20, 0, 0), (20, 30, 1, 1)),
    ("2021-01-01", "Y", "X", 1.0, 900, (50, 90, 0, 0), (40, 80, 0, 2)),
]


def test_stats_only_use_earlier_fights():
    fights, rounds, fighters = make_fights(ROWS)
    X = features.build(fights, rounds, fighters, priors=PRIORS).set_index("fight_id")
    # first fight: nobody has history
    assert X.loc["f0", "ufc_fights_a"] == 0 and X.loc["f0", "ufc_fights_b"] == 0
    # second fight: X's record and minutes come only from fight 1
    assert X.loc["f1", "ufc_fights_a"] == 1 and X.loc["f1", "ufc_wins_a"] == 1
    assert X.loc["f1", "ufc_minutes_a"] == pytest.approx(15.0)
    m = 15.0 + features.PRIOR_MINUTES
    assert X.loc["f1", "slpm_a"] == pytest.approx((60 + PRIORS["slpm"] * features.PRIOR_MINUTES) / m)
    # third fight: X is corner b, with both earlier fights (1 win, 1 loss)
    assert X.loc["f2", "ufc_wins_b"] == 1 and X.loc["f2", "ufc_losses_b"] == 1
    assert X.loc["f2", "layoff_b"] == (pd.Timestamp("2021-01-01") - pd.Timestamp("2020-06-01")).days


def test_future_fights_do_not_change_past_features():
    fights, rounds, fighters = make_fights(ROWS)
    full = features.build(fights, rounds, fighters, priors=PRIORS).set_index("fight_id")
    part = features.build(fights.iloc[:2], rounds[rounds["fight_id"].isin(["f0", "f1"])], fighters,
                          priors=PRIORS).set_index("fight_id")
    cols = [c for c in full.columns if c.startswith("f_")]
    pd.testing.assert_frame_equal(full.loc[["f0", "f1"], cols], part.loc[["f0", "f1"], cols])


def test_upcoming_bout_matches_historical_path():
    fights, rounds, fighters = make_fights(ROWS)
    full = features.build(fights, rounds, fighters, priors=PRIORS).set_index("fight_id")
    up = fights.iloc[[2]].drop(columns=["result_a", "seconds"]).assign(fight_id="u0")
    X = features.build(fights.iloc[:2], rounds[rounds["fight_id"] != "f2"], fighters, upcoming=up,
                       priors=PRIORS).set_index("fight_id")
    cols = [c for c in full.columns if c.startswith("f_")]
    np.testing.assert_allclose(X.loc["u0", cols].astype(float), full.loc["f2", cols].astype(float))


def test_model_is_symmetric():
    rng = np.random.default_rng(0)
    n = 400
    df = pd.DataFrame({"f_elo": rng.normal(size=n), "f_age": rng.normal(size=n)})
    df["result_a"] = (rng.random(n) < 1 / (1 + np.exp(-(1.2 * df["f_elo"] - 0.5 * df["f_age"])))).astype(float)
    df["date"] = pd.Timestamp("2020-01-01")
    m = FightModel(["elo", "age"]).fit(df)
    flipped = df.assign(f_elo=-df["f_elo"], f_age=-df["f_age"])
    np.testing.assert_allclose(m.predict_proba(df), 1 - m.predict_proba(flipped))
    assert m.coef[0] > 0 > m.coef[1]
