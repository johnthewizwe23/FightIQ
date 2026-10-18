"""Fighters' professional records outside the UFC (regional shows, Contender Series, other promotions), from Sherdog.

The fight data only has UFC bouts, so a debutant used to look like a blank slate. These records give the model each
fighter's win rate outside the UFC, total pro fights and pro career length, always from fights strictly before the
bout date. In the walk-forward backtest that improved the stats model's log loss by about 0.003 on both 2015-2020 and
2021-2026 (better in 98-99% of card-level resamples).

`records/sherdog_fighters.csv` maps ufcstats fighter ids to Sherdog pages; `records/sherdog_fights.csv.gz` holds every
pro fight outside the UFC. New fighters on an upcoming card are looked up on Sherdog when the card is predicted
(sherdog.com allows crawling in robots.txt; one request at a time, a pause between them). If Sherdog can't be
reached the bout simply gets neutral record features, as every bout did before.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

import numpy as np
import pandas as pd

from . import config

BASE = "https://www.sherdog.com"
ORG = BASE + "/organizations/Ultimate-Fighting-Championship-UFC-2"
UFC_EVENT = re.compile(r"^(UFC|The Ultimate Fighter)", re.I)
FEATURES = ["pro_win_pct", "pro_fights", "pro_years"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
DELAY = 2.0
ONLINE = True  # tests switch this off; lookups only ever run when a card is predicted


def _dir():
    return config.ROOT / "records"


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(fighters: fighter_id -> sherdog page, nationality, dob; fights outside the UFC: sherdog, date, result, ...)."""
    d = _dir()
    if not (d / "sherdog_fighters.csv").exists():
        return (pd.DataFrame(columns=["fighter_id", "sherdog", "nationality", "dob"]),
                pd.DataFrame(columns=["sherdog", "date", "result", "method", "event"]))
    fighters = pd.read_csv(d / "sherdog_fighters.csv")
    fights = pd.read_csv(d / "sherdog_fights.csv.gz", parse_dates=["date"])
    return fighters, fights


def features(X: pd.DataFrame, first_ufc: pd.Series, fighters: pd.DataFrame | None = None,
             fights: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per bout (rows of X: id_a, id_b, date, ufc_fights_a/b): A-minus-B pro-record features, 0 unless both are known.

    pro_win_pct: wins / (wins + losses) outside the UFC, pulled toward 50% by two of each.
    pro_fights: ln(1 + pro fights so far), counting UFC bouts from the fight data.
    pro_years: years since the first pro fight (outside the UFC or in it, whichever came first).
    """
    if fighters is None or fights is None:
        fighters, fights = load()
    page = dict(zip(fighters["fighter_id"], fighters["sherdog"]))
    hist = {}
    for p, g in fights.sort_values("date").groupby("sherdog"):
        res = g["result"].to_numpy()
        hist[p] = (g["date"].to_numpy(dtype="datetime64[ns]"), np.cumsum(res == "win"), np.cumsum(res == "loss"))
    out = {f"{c}_{s}": np.full(len(X), np.nan) for c in FEATURES for s in ("a", "b")}
    dates = X["date"].to_numpy(dtype="datetime64[ns]")
    for s in ("a", "b"):
        ids, ufc = X[f"id_{s}"].to_numpy(), X[f"ufc_fights_{s}"].fillna(0).to_numpy(float)
        first_u = first_ufc.reindex(ids).to_numpy(dtype="datetime64[ns]")
        for i, fid in enumerate(ids):
            p = page.get(fid)
            if p is None:
                continue
            d = dates[i]
            if p in hist:
                ds, cw, cl = hist[p]
                k = int(np.searchsorted(ds, d, side="left"))  # fights strictly before the bout date
                w, l, n, first = (int(cw[k - 1]), int(cl[k - 1]), k, ds[0]) if k else (0, 0, 0, None)
            else:
                w = l = n = 0
                first = None
            starts = [t for t in (first, first_u[i]) if t is not None and not np.isnat(t) and t <= d]
            out[f"pro_win_pct_{s}"][i] = (w + 2) / (w + l + 4)
            out[f"pro_fights_{s}"][i] = np.log1p(n + ufc[i])
            out[f"pro_years_{s}"][i] = (d - min(starts)) / np.timedelta64(1, "D") / 365.25 if starts else 0.0
    both = ~np.isnan(out["pro_win_pct_a"]) & ~np.isnan(out["pro_win_pct_b"])
    res = pd.DataFrame(out, index=X.index)
    for c in FEATURES:
        res["f_" + c] = np.where(both, res[f"{c}_a"] - res[f"{c}_b"], 0.0)
    return res


# ----------------------------------------------------------------------------------------- live lookups
def _get(url: str, session) -> str | None:
    time.sleep(DELAY)
    try:
        r = session.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=40)
    except Exception:
        return None
    return r.text if r.status_code == 200 else None


def _parse_date(text: str):
    m = re.search(r"([A-Z][a-z]{2}) / (\d{1,2}) / (\d{4})", re.sub(r"\s+", " ", text or ""))
    return datetime.strptime(" ".join(m.groups()), "%b %d %Y").date().isoformat() if m else None


def parse_fighter(html: str) -> dict:
    """Nationality, date of birth and every pro fight (date, result, method, event) from a Sherdog fighter page."""
    from bs4 import BeautifulSoup
    s = BeautifulSoup(html, "html.parser")
    out = {"nationality": None, "dob": None, "fights": []}
    nat = s.select_one('[itemprop="nationality"]')
    out["nationality"] = nat.get_text(" ", strip=True) if nat else None
    m = re.search(r"AGE\s*\d+\s*/\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", s.get_text(" ", strip=True))
    if m:
        out["dob"] = datetime.strptime(m.group(1), "%b %d, %Y").date().isoformat()
    for t in s.select("table"):
        rows = t.select("tr")
        head = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])] if rows else []
        if not head or head[0] != "result":
            continue
        title = t.find_previous(string=re.compile(r"Fight History - (Pro|Amateur|Pro Exhibition)", re.I))
        if title and ("amateur" in title.lower() or "exhibition" in title.lower()):
            continue
        for tr in rows[1:]:
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            ev = tds[2].select_one('a[href*="/events/"]')
            meth = tds[3].get_text(" ", strip=True).lower()
            out["fights"].append({
                "date": _parse_date(tds[2].get_text(" ", strip=True)),
                "result": tds[0].get_text(" ", strip=True).lower(),
                "method": "ko" if meth.startswith(("ko", "tko")) else "sub" if meth.startswith("submission") else
                          "dec" if meth.startswith("decision") else "other",
                "event": ev.get_text(" ", strip=True) if ev else tds[2].get_text(" ", strip=True),
            })
    return out


def _card_pages(event_date, session) -> list[list[str]]:
    """Fighter-page pairs for every bout of the Sherdog UFC event within a day of `event_date`."""
    from bs4 import BeautifulSoup
    html = _get(ORG, session)
    if not html:
        return []
    s = BeautifulSoup(html, "html.parser")
    target = pd.Timestamp(event_date).normalize()
    for tr in s.select("tr"):
        a, tds = tr.select_one('a[href*="/events/"]'), tr.find_all("td")
        if not a or not tds:
            continue
        try:
            d = pd.Timestamp(datetime.strptime(re.sub(r"\s+", " ", tds[0].get_text(" ", strip=True)), "%b %d %Y"))
        except ValueError:
            continue
        if abs((d - target).days) <= 1:
            ev = _get(BASE + a["href"], session)
            if not ev:
                return []
            e = BeautifulSoup(ev, "html.parser")
            pairs, seen = [], set()
            for block in e.select("div.fight_card, div.fight-card, section, table tr"):
                links = []
                for x in block.select('a[href*="/fighter/"]'):
                    h = x["href"].split("?")[0]
                    if h not in links:
                        links.append(h)
                if len(links) == 2 and frozenset(links) not in seen:
                    seen.add(frozenset(links))
                    pairs.append(links)
            return pairs
    return []


def _page_name(path: str) -> str:
    return re.sub(r"-\d+$", "", path.rstrip("/").rsplit("/", 1)[1]).replace("-", " ")


def ensure(card: pd.DataFrame, event_date) -> int:
    """Look up fighters on this card that have no Sherdog record yet (card rows: fighter_a/b, id_a/b).

    Returns how many were added. Never raises: without Sherdog the card just gets neutral record features.
    """
    if not ONLINE:
        return 0
    try:
        import requests
        from .odds import similarity
        fighters, fights = load()
        known = set(fighters["fighter_id"])
        need = [(r[f"id_{s}"], r[f"fighter_{s}"]) for _, r in card.iterrows() for s in ("a", "b")
                if isinstance(r.get(f"id_{s}"), str) and r[f"id_{s}"] and r[f"id_{s}"] not in known]
        if not need:
            return 0
        session = requests.Session()
        pairs = _card_pages(event_date, session)
        pages = {p for pair in pairs for p in pair}
        new_f, new_x = [], []
        for fid, name in need:
            best = max(pages, key=lambda p: similarity(name, _page_name(p)), default=None)
            if best is None or similarity(name, _page_name(best)) < 0.8:
                continue
            html = _get(BASE + best, session)
            if not html:
                continue
            rec = parse_fighter(html)
            new_f.append({"fighter_id": fid, "sherdog": best, "nationality": rec["nationality"], "dob": rec["dob"]})
            for f in rec["fights"]:
                if f["date"] and f["result"] in ("win", "loss", "draw", "nc") and not UFC_EVENT.match(f["event"] or ""):
                    new_x.append({"sherdog": best, **f})
        if not new_f:
            return 0
        d = _dir()
        d.mkdir(exist_ok=True)
        pd.concat([fighters, pd.DataFrame(new_f)]).drop_duplicates("fighter_id", keep="last").to_csv(
            d / "sherdog_fighters.csv", index=False)
        old = fights.assign(date=fights["date"].dt.strftime("%Y-%m-%d")) if len(fights) else fights
        pd.concat([old[~old["sherdog"].isin({f["sherdog"] for f in new_f})], pd.DataFrame(new_x)]).drop_duplicates(
        ).sort_values(["sherdog", "date"]).to_csv(d / "sherdog_fights.csv.gz", index=False)
        print(f"Sherdog records added for {len(new_f)} fighter(s) on this card")
        return len(new_f)
    except Exception as exc:  # never let a records lookup stop a prediction
        print(f"Sherdog records not updated: {str(exc).splitlines()[0][:120]}")
        return 0
