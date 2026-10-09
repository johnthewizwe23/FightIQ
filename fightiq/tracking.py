"""Keep the Tracking sheet up to date.

Every bout that has odds in the Predictor is logged as a row (pick, probability, suggested bet and
stake). Rows are matched by date and fighters, so re-running the same week refreshes them instead of
adding duplicates. Once a bout is in the results data its W/L is filled in; settled rows are never
changed again. Rows typed by hand are kept as they are.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import numpy as np
import pandas as pd

from .odds import _clean, similarity

COLUMNS = ["Date", "Event", "Fight", "Pick", "FightIQ prob.", "Pick result (W/L)", "Bet on", "Bet odds",
           "Stake (units)", "Bet result (W/L)"]
DATE, EVENT, FIGHT, PICK, PROB, PICK_RES, BET_ON, BET_ODDS, STAKE, BET_RES = range(10)
UNITS_PER_BANKROLL = 100  # 1 unit = 1% of bankroll
EXAMPLE_TAG = "(example row)"


def split_fight(fight: str) -> list[str]:
    return [s.strip() for s in re.split(r"\s+vs\.?\s+", str(fight), maxsplit=1, flags=re.I)]


def _to_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(v).date()
    except (ValueError, TypeError):
        return None


def row_key(row: list) -> tuple | None:
    d = _to_date(row[DATE])
    names = split_fight(row[FIGHT] or "")
    if d is None or len(names) != 2:
        return None
    return d, frozenset(_clean(n) for n in names)


def picks_to_rows(picks: pd.DataFrame, require_odds: bool = True) -> list[list]:
    """Tracking rows for a predict_card() result: every bout with odds (or every bout)."""
    rows = []
    for _, p in picks.iterrows():
        if require_odds and (pd.isna(p["Odds A"]) or pd.isna(p["Odds B"])):
            continue
        bet = p["Bet"] if isinstance(p["Bet"], str) and p["Bet"] else None
        stake = p["Stake (% bankroll)"]
        rows.append([
            pd.Timestamp(p["Date"]).to_pydatetime(), p["Event"], f"{p['Fighter A']} vs {p['Fighter B']}",
            p["Pick"], round(float(p["Pick probability"]), 4), None,
            bet, round(float(p["Bet odds"]), 2) if bet else None,
            round(float(stake) * UNITS_PER_BANKROLL, 2) if bet and not pd.isna(stake) else None, None,
        ])
    return rows


def merge(existing: list[list], new_rows: list[list]) -> list[list]:
    """Add new rows; refresh rows for the same bout that aren't settled yet; drop the example row."""
    out = [list(r) + [None] * (len(COLUMNS) - len(r)) for r in existing
           if EXAMPLE_TAG not in str(r[EVENT] or "")]
    index = {k: i for i, r in enumerate(out) if (k := row_key(r)) is not None}
    for r in new_rows:
        k = row_key(r)
        if k in index:
            if out[index[k]][PICK_RES] in (None, ""):  # not settled: refresh with the latest odds and pick
                out[index[k]] = list(r)
        else:
            index[k] = len(out)
            out.append(list(r))
    return sorted(out, key=lambda r: (_to_date(r[DATE]) or date.max))


def _outcome(side: str, name_a: str, name_b: str, result_a: float) -> str:
    if np.isnan(result_a):
        return "NC"
    if result_a == 0.5:
        return "D"
    winner, loser = (name_a, name_b) if result_a == 1 else (name_b, name_a)
    # names can be spelled differently from the results data (bookmaker spellings), so compare similarity
    return "W" if similarity(side, winner) >= similarity(side, loser) else "L"


def _fuzzy_hits(names: list[str], d: date, fights_by_day: dict) -> list:
    """Bouts within 3 days whose two fighters match `names` closely, in either order."""
    hits = []
    for offset in range(-3, 4):
        for f in fights_by_day.get(d + pd.Timedelta(days=offset).to_pytimedelta(), []):
            same = min(similarity(names[0], f.name_a), similarity(names[1], f.name_b))
            swap = min(similarity(names[0], f.name_b), similarity(names[1], f.name_a))
            if max(same, swap) >= 0.85:
                hits.append(f)
    return hits


def settle(rows: list[list], fights: pd.DataFrame, today: date | None = None) -> int:
    """Fill W/L for unsettled rows from the results data. Returns how many rows were settled."""
    today = today or date.today()
    by_pair: dict[frozenset, list] = {}
    by_day: dict[date, list] = {}
    for f in fights.itertuples():
        by_pair.setdefault(frozenset((_clean(f.name_a), _clean(f.name_b))), []).append(f)
        by_day.setdefault(f.date.date(), []).append(f)
    event_days = set(fights["date"].dt.date)
    settled = 0
    for r in rows:
        if r[PICK_RES] not in (None, "") or not r[PICK]:
            continue
        k = row_key(r)
        if k is None:
            continue
        d, pair = k
        hits = [f for f in by_pair.get(pair, []) if abs((f.date.date() - d).days) <= 3]
        if not hits:
            hits = _fuzzy_hits(split_fight(r[FIGHT]), d, by_day)
        if hits:
            f = hits[0]
            r[PICK_RES] = _outcome(r[PICK], f.name_a, f.name_b, f.result_a)
            if r[BET_ON]:
                r[BET_RES] = _outcome(r[BET_ON], f.name_a, f.name_b, f.result_a)
            settled += 1
        elif (today - d).days >= 2 and any(abs((e - d).days) <= 1 for e in event_days):
            # the event is in the results but this bout isn't: it was cancelled or changed
            r[PICK_RES] = "Cancelled"
            if r[BET_ON]:
                r[BET_RES] = "Cancelled"
            settled += 1
    return settled
