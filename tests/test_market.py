import numpy as np
import pandas as pd

from fightiq import config, data
from fightiq import evaluate as ev


def test_power_devig_is_a_fair_two_way_split():
    a = np.array([1.45, 1.91, 1.08, 2.10, 3.40])
    b = np.array([2.85, 1.91, 9.50, 2.05, 1.30])
    p, q = ev.devig(a, b), ev.devig(b, a)
    assert np.allclose(p + q, 1, atol=1e-12)
    assert np.isclose(ev.devig(1.91, 1.91), 0.5)
    # with a normal margin (book over 100%), more of it comes off the longshot than with a proportional split
    prop = (1 / a) / (1 / a + 1 / b)
    margin = 1 / a + 1 / b > 1
    a, b, p, prop = a[margin], b[margin], p[margin], prop[margin]
    fav = a < b
    assert (p[fav] >= prop[fav] - 1e-12).all() and (p[~fav] <= prop[~fav] + 1e-12).all()
    # converged: (1/a)^k + (1/b)^k = 1 for the k it found
    k = np.log(p) / np.log(1 / a)  # (the 2.10/2.05 pair, a best-price book under 100%, also converges: checked above)
    assert np.allclose((1 / a) ** k + (1 / b) ** k, 1, atol=1e-12)
    assert np.isnan(ev.devig(np.nan, 2.0))


def test_load_odds_drops_garbled_prices(tmp_path, monkeypatch):
    pd.DataFrame({"date": ["2024-01-01"] * 3, "R_fighter": ["A One", "B Two", "C Three"],
                  "B_fighter": ["X One", "Y Two", "Z Three"],
                  "R_odds": [-150, 260, 140], "B_odds": [130, 360, -900]}).to_csv(tmp_path / config.ODDS_FILE, index=False)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "ODDS_HISTORY", tmp_path / "none.csv")
    monkeypatch.setattr(config, "SITE_DATA", tmp_path / "none")
    odds = data.load_odds()
    # -150/+130 is a normal ~104% book; +260/+360 implies 49%, +140/-900 implies 132%: both impossible for one bookmaker
    assert list(odds["key_r"]) == ["a one"]


def test_load_odds_prefers_closing_ranges_then_published_cards(tmp_path, monkeypatch):
    import json
    pd.DataFrame({"date": ["2024-01-01", "2024-02-01"], "R_fighter": ["A One", "B Two"], "B_fighter": ["X One", "Y Two"],
                  "R_odds": [-150, 120], "B_odds": [130, -140]}).to_csv(tmp_path / config.ODDS_FILE, index=False)
    pd.DataFrame({"date": ["2024-01-01"], "fighter_a": ["X One"], "fighter_b": ["A One"], "open_a": [100], "open_b": [-120],
                  "close_lo_a": [110], "close_hi_a": [130], "close_lo_b": [-160], "close_hi_b": [-140]}
                 ).to_csv(tmp_path / "bfo.csv", index=False)
    (tmp_path / "site" / "weeks").mkdir(parents=True)
    week = {"date": "2026-10-17", "locks_at": "2026-10-17T21:10:00+00:00", "bouts": [
        {"pick": "C Three", "opponent": "Z Three", "odds": 1.8, "opp_odds": 2.1},
        {"pick": "D Four", "opponent": "W Four", "odds": None, "opp_odds": None}]}
    (tmp_path / "site" / "weeks" / "2026-10-17_x.json").write_text(json.dumps(week))
    (tmp_path / "site" / "weeks" / "2099-01-01_y.json").write_text(json.dumps(dict(week, date="2099-01-01",
                                                                                    locks_at="2099-01-01T00:00:00+00:00")))
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "ODDS_HISTORY", tmp_path / "bfo.csv")
    monkeypatch.setattr(config, "SITE_DATA", tmp_path / "site")
    odds = data.load_odds()
    assert len(odds) == 3  # one row per bout: the closing range replaced the A One/X One row from the public file
    bfo = odds[odds["key_r"] == "x one"].iloc[0]
    assert np.isclose(1 / bfo["odds_r"], (1 / 2.10 + 1 / 2.30) / 2)  # middle of the closing range, in probability
    assert set(odds["key_r"]) == {"x one", "b two", "c three"}  # the card that hasn't locked adds nothing


def test_odds_of_one_or_less_count_as_missing():
    from fightiq.predict import predict_card
    card = pd.DataFrame({"fighter_a": ["Max Holloway", "Max Holloway"], "fighter_b": ["Dustin Poirier", "Dustin Poirier"],
                         "odds_a": [0.9, 2.10], "odds_b": [3.0, 1.80]})
    picks, _ = predict_card(card, "test", pd.Timestamp("2026-10-17").to_pydatetime())
    assert pd.isna(picks.loc[0, "Market P(A)"]) and pd.isna(picks.loc[0, "Odds A"]) and not picks.loc[0, "Bet"]
    assert picks.loc[1, "Market P(A)"] > 0
