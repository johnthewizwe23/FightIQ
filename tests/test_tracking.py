from datetime import date, datetime

import numpy as np
import pandas as pd

from fightiq import tracking as t


def row(d, fight, pick, prob=0.6, bet=None, odds=None, stake=None, res=None, bet_res=None, event="UFC X"):
    return [datetime.fromisoformat(d), event, fight, pick, prob, res, bet, odds, stake, bet_res]


FIGHTS = pd.DataFrame({
    "date": pd.to_datetime(["2026-10-10"] * 4),
    "name_a": ["Brendan Allen", "Matheus Camilo", "Loopy Godinez", "Andre Fili"],
    "name_b": ["Christian Leroy Duncan", "Jai Herbert", "Ketlen Souza", "Kai Kamaka III"],
    "result_a": [0.0, 1.0, 0.5, np.nan],
})


def test_merge_refreshes_unsettled_keeps_settled_and_drops_example():
    existing = [row("2026-10-03", "Figueiredo vs Talbott", "Payton Talbott", event="UFC 332 (example row)"),
                row("2026-10-10", "Brendan Allen vs Christian Leroy Duncan", "Brendan Allen", prob=0.55),
                row("2026-10-03", "Natalia Silva vs Wang Cong", "Natalia Silva", res="W")]
    new = [row("2026-10-10", "Christian Leroy Duncan vs Brendan Allen", "Christian Leroy Duncan", prob=0.52),
           row("2026-10-03", "Natalia Silva vs Wang Cong", "Wang Cong", res=None),
           row("2026-10-10", "Matheus Camilo vs Jai Herbert", "Matheus Camilo")]
    out = t.merge(existing, new)
    assert len(out) == 3 and not any("example" in r[t.EVENT] for r in out)
    by_fight = {r[t.FIGHT]: r for r in out}
    assert by_fight["Christian Leroy Duncan vs Brendan Allen"][t.PICK] == "Christian Leroy Duncan"
    assert by_fight["Natalia Silva vs Wang Cong"][t.PICK] == "Natalia Silva"  # settled rows never change


def test_settle_fills_results_draws_no_contests_and_cancellations():
    rows = [row("2026-10-10", "Brendan Allen vs Christian Leroy Duncan", "Brendan Allen",
                bet="Christian Leroy Duncan", odds=2.6, stake=1.5),
            row("2026-10-10", "Jai Herbert vs Matheus Camilo", "Matheus Camilo"),
            row("2026-10-10", "Loopy Godinez vs Ketlen Souza", "Loopy Godinez"),
            row("2026-10-10", "Andre Fili vs Kai Kamaka III", "Andre Fili"),
            row("2026-10-10", "Julius Walker vs Gerald Meerschaert", "Julius Walker"),
            row("2026-10-17", "Next Week vs Someone", "Next Week")]
    n = t.settle(rows, FIGHTS, today=date(2026, 10, 14))
    assert [r[t.PICK_RES] for r in rows] == ["L", "W", "D", "NC", "Cancelled", None]
    assert rows[0][t.BET_RES] == "W" and n == 5


def test_picks_to_rows_skips_bouts_without_odds():
    picks = pd.DataFrame([
        {"Date": date(2026, 10, 10), "Event": "UFC FN", "Fighter A": "A B", "Fighter B": "C D", "Pick": "A B",
         "Pick probability": 0.61234, "Odds A": 1.8, "Odds B": 2.1, "Bet": "C D", "Bet odds": 2.1,
         "Stake (% bankroll)": 0.0123},
        {"Date": date(2026, 10, 10), "Event": "UFC FN", "Fighter A": "E F", "Fighter B": "G H", "Pick": "E F",
         "Pick probability": 0.7, "Odds A": np.nan, "Odds B": np.nan, "Bet": "", "Bet odds": np.nan,
         "Stake (% bankroll)": np.nan},
    ])
    rows = t.picks_to_rows(picks)
    assert len(rows) == 1
    assert rows[0][t.FIGHT] == "A B vs C D" and rows[0][t.STAKE] == 1.23 and rows[0][t.PROB] == 0.6123
