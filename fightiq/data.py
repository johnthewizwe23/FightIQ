"""Download and clean the raw ufcstats data.

Produces two tables:
  fights  - one row per bout (date, fighters, result, duration, division)
  rounds  - per-fighter, per-bout striking/grappling totals (summed over rounds)
"""
from __future__ import annotations

import re
import time
import unicodedata

import numpy as np
import pandas as pd
import requests

from . import config

WEIGHT_LIMITS = {
    "strawweight": 115, "flyweight": 125, "bantamweight": 135, "featherweight": 145,
    "lightweight": 155, "welterweight": 170, "middleweight": 185,
    "light heavyweight": 205, "heavyweight": 265,
}


def download(force: bool = False, max_age_hours: float | None = None) -> None:
    """Fetch the latest CSVs into data/raw.

    Existing files are kept unless `force`, or they are older than `max_age_hours`.
    """
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    targets = [(f"{config.GRECO_BASE}/{f}", config.RAW_DIR / f) for f in config.GRECO_FILES]
    targets.append((config.ODDS_URL, config.RAW_DIR / config.ODDS_FILE))
    for url, path in targets:
        stale = max_age_hours is not None and path.exists() and (time.time() - path.stat().st_mtime) / 3600 > max_age_hours
        if path.exists() and not force and not stale:
            continue
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        print(f"downloaded {path.name} ({len(resp.content) / 1e6:.1f} MB)")


def norm_name(name: str) -> str:
    """Lower-case, accent-free, punctuation-free name used for matching."""
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def parse_of(value) -> tuple[float, float]:
    """'16 of 40' -> (16, 40)."""
    m = re.match(r"\s*(\d+)\s+of\s+(\d+)", str(value))
    return (float(m.group(1)), float(m.group(2))) if m else (np.nan, np.nan)


def parse_clock(value) -> float:
    """'4:32' -> 272 seconds."""
    m = re.match(r"\s*(\d+):(\d{2})", str(value))
    return float(m.group(1)) * 60 + float(m.group(2)) if m else np.nan


def fight_seconds(final_round, final_time, time_format) -> float:
    """Total fight time from the final round, its clock time and the format string."""
    t = parse_clock(final_time)
    try:
        r = int(final_round)
    except (TypeError, ValueError):
        return np.nan
    if np.isnan(t):
        return np.nan
    m = re.search(r"\(([\d\-]+)\)", str(time_format))
    lengths = [int(x) for x in m.group(1).split("-")] if m else []
    full = sum(lengths[: r - 1]) if len(lengths) >= r - 1 else 5 * (r - 1)
    return full * 60 + t


def parse_division(weightclass: str) -> tuple[str, bool, bool]:
    """'UFC Women's Flyweight Title Bout' -> ('flyweight', women=True, title=True)."""
    w = str(weightclass).lower()
    women = "women" in w
    title = "title" in w
    for name in sorted(WEIGHT_LIMITS, key=len, reverse=True):
        if name in w:
            return name, women, title
    return "other", women, title


def _parse_height(v) -> float:
    m = re.match(r"\s*(\d+)'\s*(\d+)", str(v))
    return float(m.group(1)) * 12 + float(m.group(2)) if m else np.nan


def _parse_inches(v) -> float:
    m = re.match(r"\s*([\d.]+)", str(v))
    return float(m.group(1)) if m else np.nan


def _parse_weight(v) -> float:
    m = re.match(r"\s*(\d+)", str(v))
    return float(m.group(1)) if m else np.nan


def load_fighters() -> pd.DataFrame:
    """Tale of the tape: one row per fighter keyed by ufcstats URL."""
    tt = pd.read_csv(config.RAW_DIR / "ufc_fighter_tott.csv")
    out = pd.DataFrame({
        "fighter_id": tt["URL"].str.strip(),
        "name": tt["FIGHTER"].str.strip(),
        "height": tt["HEIGHT"].map(_parse_height),
        "reach": tt["REACH"].map(_parse_inches),
        "weight": tt["WEIGHT"].map(_parse_weight),
        "stance": tt["STANCE"],
        "dob": pd.to_datetime(tt["DOB"], format="%b %d, %Y", errors="coerce"),
    })
    out["key"] = out["name"].map(norm_name)
    return out.drop_duplicates("fighter_id")


class FighterResolver:
    """Map a fighter name (plus bout weight) to a ufcstats fighter id.

    ufcstats has a handful of name collisions (two Bruno Silvas, etc.); those are
    split by the weight class of the bout.
    """

    SUFFIX = re.compile(r"\s+(jr|sr|ii|iii|iv)$")

    def __init__(self, fighters: pd.DataFrame):
        self.by_key: dict[str, list[tuple[str, float]]] = {}
        self.by_loose: dict[str, list[tuple[str, float]]] = {}
        for r in fighters.itertuples():
            self.by_key.setdefault(r.key, []).append((r.fighter_id, r.weight))
            self.by_loose.setdefault(self.SUFFIX.sub("", r.key), []).append((r.fighter_id, r.weight))

    def resolve(self, name: str, weight_limit: float | None = None) -> str:
        key = norm_name(name)
        cands = self.by_key.get(key) or self.by_loose.get(self.SUFFIX.sub("", key))
        if not cands:
            return "name:" + key
        if len(cands) == 1 or weight_limit is None or np.isnan(weight_limit):
            return cands[0][0]
        return min(cands, key=lambda c: abs((c[1] if c[1] == c[1] else 999) - weight_limit))[0]


def load_fights() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (fights, rounds) with fighter ids resolved and results decoded."""
    raw = config.RAW_DIR
    events = pd.read_csv(raw / "ufc_event_details.csv")
    events["EVENT"] = events["EVENT"].str.strip()
    events["date"] = pd.to_datetime(events["DATE"].str.strip(), format="%B %d, %Y")
    events = events.drop_duplicates("EVENT")

    res = pd.read_csv(raw / "ufc_fight_results.csv")
    res = res.drop_duplicates("URL").copy()
    res["EVENT"] = res["EVENT"].str.strip()
    res["BOUT"] = res["BOUT"].str.strip()
    res = res.merge(events[["EVENT", "date"]], on="EVENT", how="inner")

    names = res["BOUT"].str.split(r"\s+vs\.\s+", n=1, expand=True, regex=True)
    res["name_a"] = names[0].str.strip()
    res["name_b"] = names[1].str.strip()
    res = res.dropna(subset=["name_a", "name_b"])

    outcome = res["OUTCOME"].str.strip().str.upper()
    res["result_a"] = outcome.map({"W/L": 1.0, "L/W": 0.0, "D/D": 0.5})  # NC -> NaN
    div = res["WEIGHTCLASS"].map(parse_division)
    res["division"] = [d[0] for d in div]
    res["women"] = [d[1] for d in div]
    res["title"] = [d[2] for d in div]
    res["seconds"] = [fight_seconds(r, t, f) for r, t, f in zip(res["ROUND"], res["TIME"], res["TIME FORMAT"])]
    res["method"] = res["METHOD"].str.strip()
    res["scheduled_rounds"] = res["TIME FORMAT"].str.extract(r"^(\d+)")[0].astype(float)

    fighters = load_fighters()
    resolver = FighterResolver(fighters)
    limit = res["division"].map(WEIGHT_LIMITS).astype(float)
    res["id_a"] = [resolver.resolve(n, w) for n, w in zip(res["name_a"], limit)]
    res["id_b"] = [resolver.resolve(n, w) for n, w in zip(res["name_b"], limit)]

    # Within an event, ufcstats lists bouts main event first; reverse it so
    # same-night tournament bouts run in the order they happened.
    res["bout_order"] = res.groupby("EVENT").cumcount(ascending=False)
    res = res.sort_values(["date", "EVENT", "bout_order"]).reset_index(drop=True)
    res["fight_id"] = res["URL"].str.strip()

    fights = res[["fight_id", "EVENT", "BOUT", "date", "name_a", "name_b", "id_a", "id_b", "result_a",
                  "method", "division", "women", "title", "scheduled_rounds", "seconds"]].rename(
        columns={"EVENT": "event", "BOUT": "bout"})

    rounds = _load_round_totals(raw, fights)
    return fights, rounds


def _load_round_totals(raw, fights: pd.DataFrame) -> pd.DataFrame:
    fs = pd.read_csv(raw / "ufc_fight_stats.csv")
    fs["EVENT"] = fs["EVENT"].str.strip()
    fs["BOUT"] = fs["BOUT"].str.strip()
    fs = fs.dropna(subset=["FIGHTER"])
    sig = fs["SIG.STR."].map(parse_of)
    td = fs["TD"].map(parse_of)
    head = fs["HEAD"].map(parse_of)
    tot = pd.DataFrame({
        "event": fs["EVENT"], "bout": fs["BOUT"], "fighter_key": fs["FIGHTER"].map(norm_name),
        "sig_l": [x[0] for x in sig], "sig_a": [x[1] for x in sig],
        "td_l": [x[0] for x in td], "td_a": [x[1] for x in td],
        "head_l": [x[0] for x in head],
        "kd": pd.to_numeric(fs["KD"], errors="coerce"),
        "sub_att": pd.to_numeric(fs["SUB.ATT"], errors="coerce"),
        "ctrl": fs["CTRL"].map(parse_clock),
    })
    tot = tot.groupby(["event", "bout", "fighter_key"], as_index=False).sum(min_count=1)

    # attach fight_id and which side (a/b) each row belongs to
    keys = fights[["fight_id", "event", "bout", "name_a", "name_b"]].copy()
    keys["key_a"] = keys["name_a"].map(norm_name)
    keys["key_b"] = keys["name_b"].map(norm_name)
    tot = tot.merge(keys, on=["event", "bout"], how="inner")
    tot["side"] = np.where(tot["fighter_key"] == tot["key_a"], "a",
                           np.where(tot["fighter_key"] == tot["key_b"], "b", None))
    tot = tot.dropna(subset=["side"])
    cols = ["sig_l", "sig_a", "td_l", "td_a", "head_l", "kd", "sub_att", "ctrl"]
    return tot[["fight_id", "side"] + cols].drop_duplicates(["fight_id", "side"])


# A two-way price from one bookmaker implies a total of about 102-106%. A few rows in the public odds file are
# garbled (the two prices imply under 100% or over 115%, impossible for one book); they are dropped.
BOOK_RANGE = (1.0, 1.15)


def load_odds() -> pd.DataFrame:
    """Historical closing-ish odds as decimal prices, keyed by date and normalised names."""
    m = pd.read_csv(config.RAW_DIR / config.ODDS_FILE)

    def to_decimal(american):
        a = pd.to_numeric(american, errors="coerce")
        return np.where(a > 0, 1 + a / 100, 1 + 100 / a.abs())

    out = pd.DataFrame({
        "date": pd.to_datetime(m["date"]),
        "key_r": m["R_fighter"].map(norm_name),
        "key_b": m["B_fighter"].map(norm_name),
        "odds_r": to_decimal(m["R_odds"]),
        "odds_b": to_decimal(m["B_odds"]),
    }).dropna()
    book = 1 / out["odds_r"] + 1 / out["odds_b"]
    return out[(book >= BOOK_RANGE[0]) & (book <= BOOK_RANGE[1])].reset_index(drop=True)
