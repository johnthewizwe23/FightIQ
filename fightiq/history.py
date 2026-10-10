"""Every past UFC fight with the pick FightIQ would have made at the time, for the website's fight finder.

Each year is predicted by a model fitted only on earlier years. From 2006 on it trains on fights since 2005,
exactly like the walk-forward backtest (2015 on is the backtest itself, blend and bets included). Before 2006
the model learns from every earlier UFC fight, which isn't many, so those picks are rough. The first year in
the data has nothing earlier to learn from and gets no pick.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import data, features
from . import evaluate as ev
from .pipeline import BET_THRESHOLD, C, build_history

EARLY_TRAIN_START = "1990-01-01"  # before the backtest's own window, learn from every earlier fight
COLUMNS = ["event", "fighter_a", "fighter_b", "result_a", "p_model", "p_fightiq", "odds_a", "odds_b", "bet",
           "debut", "division", "method"]
METHODS = {"Decision - Unanimous": "Unanimous decision", "Decision - Split": "Split decision",
           "Decision - Majority": "Majority decision", "TKO - Doctor's Stoppage": "TKO (doctor)",
           "Could Not Continue": "Could not continue"}


def predictions(X: pd.DataFrame) -> pd.DataFrame:
    """Walk-forward model win chance for every bout, the blend where odds exist, and the backtest's bets."""
    first, last = int(X["date"].dt.year.min()), int(X["date"].dt.year.max())
    split = int(ev.TRAIN_START[:4]) + 1
    P = pd.concat([
        ev.walk_forward(X, features.FEATURES_FULL, C, years=range(first + 1, split),
                        train_start=EARLY_TRAIN_START, all_results=True),
        ev.walk_forward(X, features.FEATURES_FULL, C, years=range(split, last + 1), all_results=True),
    ], ignore_index=True)
    decided = P["result_a"].isin([0.0, 1.0])
    # fitted on exactly the rows the backtest uses, so the blend and bets match reports/evaluation.md
    P["p_blend"] = ev.walk_forward_blend(P[decided & (P["year"] >= ev.TEST_YEARS.start)])["p_blend"]

    ok = (P["p_blend"].notna() & P["odds_a"].notna() & P["odds_b"].notna() & decided
          & (P["ufc_fights_a"] > 0) & (P["ufc_fights_b"] > 0))
    ev_a, ev_b = ev.expected_value(P["p_blend"], P["odds_a"], P["odds_b"])
    P["bet"] = np.where(ok & (ev_a > BET_THRESHOLD) & (ev_a >= ev_b), "a",
                        np.where(ok & (ev_b > BET_THRESHOLD) & (ev_b > ev_a), "b", None))
    out = X.merge(P[["fight_id", "p_model", "p_blend", "bet"]], on="fight_id", how="left")
    return out.sort_values(["date", "bout_seq"], kind="stable").reset_index(drop=True)


def _division(division: str, women: bool, title: bool) -> str:
    name = "Other weight" if division == "other" else ("Women's " if women else "") + division.title()
    return name + (" title" if title else "")


def to_json(P: pd.DataFrame) -> str:
    """Compact JSON: lookup lists for events/divisions/methods, then one fight per line, oldest first."""
    events, divisions, methods = {}, {}, {}

    def idx(table, key):
        return table.setdefault(key, len(table))

    def num(v, digits):
        return None if pd.isna(v) else round(float(v), digits)

    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    lines = []
    for r in P.itertuples():
        e = idx(events, (r.date.strftime("%Y-%m-%d"), r.event))
        res = None if pd.isna(r.result_a) else (int(r.result_a) if r.result_a in (0, 1) else 0.5)
        debut = int(r.ufc_fights_a == 0) + 2 * int(r.ufc_fights_b == 0)
        row = [e, r.name_a, r.name_b, res, num(r.p_model, 3), num(r.p_blend, 3), num(r.odds_a, 2), num(r.odds_b, 2),
               r.bet if isinstance(r.bet, str) else None, debut,
               idx(divisions, _division(r.division, bool(r.women), bool(r.title))),
               idx(methods, METHODS.get(r.method, r.method) if isinstance(r.method, str) else "")]
        lines.append(dump(row))
    head = {
        "about": "Each fight's pick comes from a model trained only on earlier years (walk-forward). "
                 "p_model / p_fightiq / odds are for fighter_a; result_a: 1 = A won, 0 = B won, 0.5 = draw, "
                 "null = no contest. bet = the side the backtest bet rule backed (flat 1 unit). "
                 "debut: 1 = A, 2 = B, 3 = both making their UFC debut.",
        "fights_to": P["date"].max().strftime("%Y-%m-%d"),
        "bet_threshold": BET_THRESHOLD,
        "columns": COLUMNS,
        "divisions": list(divisions),
        "methods": list(methods),
    }
    # lookups are in order of first appearance and rows oldest first, so each update only appends lines
    out = ["{"] + [f" {dump(k)}: {dump(v)}," for k, v in head.items()]
    out += [' "events": [', ",\n".join(dump(list(k)) for k in events), " ],",
            ' "fights": [', ",\n".join(lines), " ]", "}", ""]
    return "\n".join(out)


def write(path: Path) -> int:
    """Rebuild the fight-finder file from the current data. Returns the number of fights."""
    X, *_ = build_history()
    P = predictions(ev.attach_odds(X, data.load_odds()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(P), encoding="utf-8")
    return len(P)
