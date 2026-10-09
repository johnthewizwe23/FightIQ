"""Live fight odds from The Odds API (https://the-odds-api.com).

A free account gives an API key and 500 requests a month; each fetch here costs 1.
The key is kept in .odds_api_key in the project folder (never committed) or the
ODDS_API_KEY environment variable.
"""
from __future__ import annotations

import difflib
import os
import re

import numpy as np
import pandas as pd
import requests

from . import config
from .data import norm_name

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "mma_mixed_martial_arts"
KEY_FILE = config.ROOT / ".odds_api_key"
DEFAULT_REGION = "au"  # Australian bookmakers; also us, us2, uk, eu
MATCH_THRESHOLD = 0.8


class OddsError(Exception):
    pass


def load_key() -> str | None:
    key = os.environ.get("ODDS_API_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
    return key or None


def save_key(key: str) -> None:
    KEY_FILE.write_text(key.strip() + "\n")


def check_key(key: str) -> None:
    """Validate a key with the sports list, which doesn't use any of the monthly quota."""
    resp = requests.get(f"{API_BASE}/sports", params={"apiKey": key}, timeout=30)
    if resp.status_code == 401:
        raise OddsError("The Odds API rejected that key - copy it again from the email they sent you.")
    resp.raise_for_status()


def fetch_events(key: str, region: str = DEFAULT_REGION) -> tuple[list[dict], str | None]:
    resp = requests.get(f"{API_BASE}/sports/{SPORT}/odds/",
                        params={"apiKey": key, "regions": region, "markets": "h2h", "oddsFormat": "decimal"},
                        timeout=30)
    if resp.status_code == 401:
        raise OddsError("The Odds API rejected the key. Save it again with: python3 -m fightiq odds-key YOUR_KEY")
    if resp.status_code == 429:
        raise OddsError("The Odds API monthly quota is used up (it resets each month).")
    if resp.status_code >= 400:
        raise OddsError(f"The Odds API returned {resp.status_code}: {resp.text[:200]}")
    return resp.json(), resp.headers.get("x-requests-remaining")


def best_prices(events: list[dict], bookmakers: list[str] | None = None) -> pd.DataFrame:
    """Best decimal price for each fighter across bookmakers, one row per bout."""
    rows = []
    for ev in events:
        a, b = ev.get("home_team"), ev.get("away_team")
        best = {a: (np.nan, ""), b: (np.nan, "")}
        for bk in ev.get("bookmakers", []):
            if bookmakers and bk.get("key") not in bookmakers:
                continue
            for market in bk.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for o in market.get("outcomes", []):
                    name, price = o.get("name"), o.get("price")
                    if name in best and price and not (best[name][0] >= price):
                        best[name] = (float(price), bk.get("title") or bk.get("key", ""))
        rows.append({"fighter_a": a, "fighter_b": b, "odds_a": best[a][0], "odds_b": best[b][0],
                     "book_a": best[a][1], "book_b": best[b][1], "commence": ev.get("commence_time")})
    return pd.DataFrame(rows, columns=["fighter_a", "fighter_b", "odds_a", "odds_b", "book_a", "book_b", "commence"])


def _clean(name: str) -> str:
    # Fighters-sheet names can carry a division tag for shared names: "Bruno Silva (Flyweight)"
    return norm_name(re.sub(r"\s*\(.*?\)|\s*#\d+$", "", str(name)))


def similarity(x: str, y: str) -> float:
    x, y = _clean(x), _clean(y)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    score = difflib.SequenceMatcher(None, x, y).ratio()
    xs, ys = x.split(), y.split()
    if xs[-1] == ys[-1] and xs[0][0] == ys[0][0]:  # same surname and first initial
        score = max(score, 0.9)
    return score


def attach(card: pd.DataFrame, prices: pd.DataFrame, threshold: float = MATCH_THRESHOLD) -> tuple[pd.DataFrame, int]:
    """Copy odds onto matching bouts of `card` (either corner order). Unmatched bouts keep their odds."""
    card = card.copy()
    for col in ("odds_a", "odds_b"):
        if col not in card:
            card[col] = np.nan
    if "odds_source" not in card:
        card["odds_source"] = ""
    matched = 0
    for i, row in card.iterrows():
        best, best_score, flip = None, 0.0, False
        for _, p in prices.iterrows():
            same = min(similarity(row["fighter_a"], p["fighter_a"]), similarity(row["fighter_b"], p["fighter_b"]))
            swap = min(similarity(row["fighter_a"], p["fighter_b"]), similarity(row["fighter_b"], p["fighter_a"]))
            if max(same, swap) > best_score:
                best, best_score, flip = p, max(same, swap), swap > same
        if best is None or best_score < threshold or np.isnan(best["odds_a"]) or np.isnan(best["odds_b"]):
            continue
        oa, ob, ba, bb = ((best["odds_b"], best["odds_a"], best["book_b"], best["book_a"]) if flip
                          else (best["odds_a"], best["odds_b"], best["book_a"], best["book_b"]))
        card.at[i, "odds_a"], card.at[i, "odds_b"] = float(oa), float(ob)
        card.at[i, "odds_source"] = f"{row['fighter_a']}: {oa:.2f} ({ba}) | {row['fighter_b']}: {ob:.2f} ({bb})"
        matched += 1
    return card, matched


def add_live_odds(card: pd.DataFrame, region: str = DEFAULT_REGION, bookmakers: list[str] | None = None) -> pd.DataFrame:
    """Fill the card with the best current odds. Without a saved key the card is returned unchanged."""
    key = load_key()
    if not key:
        print("no odds API key saved - type the odds in yourself, or set one up with: "
              "python3 -m fightiq odds-key YOUR_KEY")
        return card
    try:
        events, remaining = fetch_events(key, region)
    except (OddsError, requests.RequestException) as exc:
        print(f"couldn't get odds ({exc}) - type them in yourself this time")
        return card
    card, n = attach(card, best_prices(events, bookmakers))
    left = f", {remaining} odds requests left this month" if remaining else ""
    print(f"odds: found {n} of {len(card)} bouts{left}")
    missing = card[card["odds_a"].isna() | card["odds_b"].isna()]
    for _, r in missing.iterrows():
        print(f"  no odds yet for {r['fighter_a']} vs {r['fighter_b']}")
    return card
