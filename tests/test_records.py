import numpy as np
import pandas as pd

from fightiq import records


def test_pro_record_uses_only_earlier_fights_and_is_neutral_when_unknown():
    fighters = pd.DataFrame({"fighter_id": ["A", "B"], "sherdog": ["/fighter/a-1", "/fighter/b-2"],
                             "nationality": ["Brazil", "USA"], "dob": [None, None]})
    fights = pd.DataFrame({"sherdog": ["/fighter/a-1"] * 3 + ["/fighter/b-2"],
                           "date": pd.to_datetime(["2018-01-01", "2019-01-01", "2020-06-01", "2017-01-01"]),
                           "result": ["win", "win", "loss", "loss"], "method": ["ko", "sub", "dec", "dec"],
                           "event": ["Regional 1", "Regional 2", "PFL 1", "Regional 3"]})
    X = pd.DataFrame({"id_a": ["A", "A", "C"], "id_b": ["B", "B", "B"], "ufc_fights_a": [0, 1, 0], "ufc_fights_b": [0, 2, 0],
                      "date": pd.to_datetime(["2019-01-01", "2021-01-01", "2021-01-01"])})
    first_ufc = pd.Series(pd.to_datetime(["2019-01-01", "2016-01-01"]), index=["A", "B"])
    F = records.features(X, first_ufc, fighters, fights)
    # bout 1 on 2019-01-01: A's fight that same day does not count (strictly earlier only): 1-0 -> (1+2)/(1+0+4)
    assert np.isclose(F.loc[0, "pro_win_pct_a"], 3 / 5) and np.isclose(F.loc[0, "pro_win_pct_b"], 2 / 5)
    assert np.isclose(F.loc[0, "pro_fights_a"], np.log1p(1)) and np.isclose(F.loc[0, "pro_years_a"], 1.0, atol=0.01)
    # bout 2: A 2-1 outside the UFC + 1 UFC fight; B's career starts at the UFC debut in 2016 (before 2017's fight)
    assert np.isclose(F.loc[1, "pro_win_pct_a"], 4 / 7) and np.isclose(F.loc[1, "pro_fights_a"], np.log1p(4))
    assert np.isclose(F.loc[1, "pro_years_b"], 5.0, atol=0.01)
    assert np.isclose(F.loc[1, "f_pro_win_pct"], 4 / 7 - 2 / 5)
    # bout 3: C has no record, so the bout gets neutral (zero) differences
    assert (F.loc[2, ["f_pro_win_pct", "f_pro_fights", "f_pro_years"]] == 0).all()


def test_sherdog_page_parser_keeps_pro_fights_only():
    html = """<html><body><span itemprop="nationality">Azerbaijan</span><div>AGE 24 / Nov 17, 2001</div>
    <h2>Fight History - Pro</h2><table><tr><td>Result</td><td>Fighter</td><td>Event</td><td>Method</td><td>R</td><td>Time</td></tr>
    <tr><td>win</td><td><a href="/fighter/x-1">X</a></td><td><a href="/events/uaew-1">UAE Warriors 69</a> Mar / 28 / 2026</td>
    <td>Submission (Choke)</td><td>1</td><td>2:00</td></tr></table>
    <h2>Fight History - Amateur</h2><table><tr><td>Result</td><td>Fighter</td><td>Event</td><td>Method</td><td>R</td><td>Time</td></tr>
    <tr><td>win</td><td>Y</td><td>Am Show Jan / 01 / 2019</td><td>Decision</td><td>3</td><td>3:00</td></tr></table></body></html>"""
    rec = records.parse_fighter(html)
    assert rec["nationality"] == "Azerbaijan" and rec["dob"] == "2001-11-17"
    assert rec["fights"] == [{"date": "2026-03-28", "result": "win", "method": "sub", "event": "UAE Warriors 69"}]
