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
    odds = data.load_odds()
    # -150/+130 is a normal ~104% book; +260/+360 implies 49%, +140/-900 implies 132%: both impossible for one bookmaker
    assert list(odds["key_r"]) == ["a one"]
