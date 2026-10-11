"""The public FightIQ website: this week's picks, the live record, and how the model works.

    python3 -m fightiq site                  # update predictions + results, then rebuild site/
    python3 -m fightiq site --render-only    # rebuild site/ from the saved state only (no network)

State lives in site_data/ (one JSON per card plus tracking.csv) and is committed, so every pick's history
is public and timestamped. A card's picks lock at the first fight's start time and never change after.
"""
from __future__ import annotations

import csv
import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import card as card_mod
from . import config, data, history
from . import odds as odds_mod
from . import report as report_mod
from . import tracking as tr

STATE = config.ROOT / "site_data"
SITE = config.ROOT / "site"
REPO_URL = "https://github.com/johnthewizwe23/FightIQ"
HISTORY_FILE = "fight_history.json"
UNITS = report_mod.UNITS_PER_BANKROLL
FEATURE_TEXT = {
    "slpm": ("Strikes landed per minute", True),
    "exp_str_acc": ("Striking accuracy against this opponent's defence", True),
    "sapm": ("Strikes absorbed per minute", False),
    "td_avg": ("Takedowns per 15 minutes", True),
    "reach": ("Reach", True),
    "age": ("Age on fight night", False),
    "elo": ("Elo rating: results weighted by quality of opposition", True),
    "ctrl_avg": ("Control time per 15 minutes", False),
    "experience": ("Number of UFC fights", False),
    "layoff": ("Time since last fight", False),
    "age_old": ("Years past age 33 (extra age penalty)", False),
    "debut": ("UFC debut", False),
}


# ============================================================================ state

def _paths(state: Path):
    return state / "weeks", state / "tracking.csv"


def read_tracking(path: Path) -> list[list]:
    if not path.exists():
        return []
    rows = []
    with open(path, newline="") as fh:
        for rec in csv.DictReader(fh):
            row = [rec.get(c, "") or None for c in tr.COLUMNS]
            for i in (tr.PROB, tr.BET_ODDS, tr.STAKE):
                row[i] = float(row[i]) if row[i] not in (None, "") else None
            rows.append(row)
    return rows


def write_tracking(path: Path, rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(tr.COLUMNS)
        for r in rows:
            d = tr._to_date(r[tr.DATE])
            w.writerow([d.isoformat() if d else ""] + ["" if v is None else v for v in r[1:]])


def load_weeks(state: Path) -> list[dict]:
    weeks_dir, _ = _paths(state)
    weeks = [json.loads(p.read_text()) for p in sorted(weeks_dir.glob("*.json"))] if weeks_dir.exists() else []
    return sorted(weeks, key=lambda w: w["date"])


def _slug(day: str, event: str) -> str:
    return f"{day}_" + re.sub(r"[^A-Za-z0-9]+", "-", event).strip("-")[:60]


def _bouts(picks: pd.DataFrame) -> list[dict]:
    bouts = []
    for r, (_, p) in zip(report_mod._rows(picks), picks.iterrows()):
        pick_is_a = r["pick"] == p["Fighter A"]
        side = lambda v: None if pd.isna(v) else round(float(v if pick_is_a else 1 - v), 4)
        bouts.append({
            "pick": r["pick"], "opponent": r["opp"], "p": round(r["p"], 4),
            "p_model": side(p["Model P(A)"]), "p_market": side(p["Market P(A)"]),
            "odds": None if r["pick_odds"] is None else round(float(r["pick_odds"]), 2),
            "opp_odds": None if r["opp_odds"] is None else round(float(r["opp_odds"]), 2),
            "debut": bool(r["debut"]),
            "bet": r["bet"], "bet_odds": None if not r["bet"] else round(float(r["bet_odds"]), 2),
            "bet_p": None if not r["bet"] else round(float(r["bet_p"]), 4),
            "edge": None if not r["bet"] else round(float(r["ev"]), 4),
            "stake": None if not r["bet"] else round(float(r["stake"]) * UNITS, 2),
        })
    return bouts


# ============================================================================ weekly update

def update(state: Path = STATE, site: Path = SITE, scrape: bool = True, now: pd.Timestamp | None = None) -> dict | None:
    """Find the next card, publish/refresh its picks until they lock, log them, and settle results."""
    from .predict import predict_card

    now = now or pd.Timestamp.now(tz="UTC")
    data.download(force=True)
    fights, _ = data.load_fights()
    weeks_dir, tracking_csv = _paths(state)
    rows = read_tracking(tracking_csv)

    events = None
    key = odds_mod.load_key()
    if key:
        try:
            events, remaining = odds_mod.fetch_events(key)
            print(f"odds feed: {len(events)} MMA bouts ({remaining} requests left this month)")
        except (odds_mod.OddsError, requests.RequestException) as exc:
            print(f"odds feed unavailable: {exc}")
    else:
        print("no odds API key (ODDS_API_KEY) - picks will be published without odds")

    card = name = day = start = None
    source = ""
    if scrape:
        try:
            card, name, day = card_mod.scrape_upcoming()
            source = "ufcstats.com"
        except Exception as exc:  # blocked, changed, or no browser: fall back to the odds feed
            print(f"ufcstats.com card unavailable ({str(exc).splitlines()[0][:160]})")
    if card is not None and events:
        card, n = odds_mod.attach(card, odds_mod.best_prices(events))
        print(f"odds found for {n} of {len(card)} bouts")
        start = _first_start(events, card, day)
    elif card is None and events:
        found = odds_mod.card_from_feed(events, fights, now)
        if found:
            card, name, day, start = found
            source = "odds feed"
    week = None
    if card is not None and len(card):
        week = _publish(card, name, pd.Timestamp(day), start, source, state, site, now, rows, predict_card)
    else:
        print("no upcoming UFC card found - results only this run")

    dropped = sync_tracking(rows, load_weeks(state))
    if dropped:
        print(f"removed {dropped} tracking rows left over from an earlier version of a card")
    n = tr.settle(rows, fights)
    print(f"results filled in: {n}")
    write_tracking(tracking_csv, rows)
    try:
        print(f"fight finder: {history.write(state / HISTORY_FILE):,} past fights")
    except Exception as exc:  # never let the fight finder hold up the picks
        print(f"fight finder not updated: {exc}")
    return week


def _bout_key(day: date, a: str, b: str) -> tuple:
    return day, frozenset(odds_mod._clean(n) for n in (a, b))


def sync_tracking(rows: list[list], weeks: list[dict]) -> int:
    """Keep each published card's tracking rows identical to its bouts.

    A card can be republished with different spellings before it locks (bookmaker names first, then the official
    ufcstats names). Rows for a card's date that are not one of its bouts are leftovers and are removed, so no bout
    is counted twice. The card files themselves (the locked picks) are never touched.
    """
    cards = {}
    for w in weeks:
        day = date.fromisoformat(w["date"])
        cards[day] = {_bout_key(day, b["pick"], b["opponent"]) for b in w["bouts"]}
    before = len(rows)
    rows[:] = [r for r in rows if (k := tr.row_key(r)) is None or k[0] not in cards or k in cards[k[0]]]
    return before - len(rows)


def _first_start(events, card, day) -> pd.Timestamp | None:
    """Earliest start time (UTC) the odds feed lists for this card's bouts."""
    prices = odds_mod.best_prices(events)
    starts = []
    for _, c in card.iterrows():
        for _, p in prices.iterrows():
            same = min(odds_mod.similarity(c["fighter_a"], p["fighter_a"]), odds_mod.similarity(c["fighter_b"], p["fighter_b"]))
            swap = min(odds_mod.similarity(c["fighter_a"], p["fighter_b"]), odds_mod.similarity(c["fighter_b"], p["fighter_a"]))
            if max(same, swap) >= odds_mod.MATCH_THRESHOLD and p["commence"]:
                starts.append(pd.Timestamp(p["commence"]))
    return min(starts) if starts else None


def _publish(card, name, day, start, source, state, site, now, rows, predict_card) -> dict:
    weeks_dir, _ = _paths(state)
    weeks_dir.mkdir(parents=True, exist_ok=True)
    day_s = day.strftime("%Y-%m-%d")
    existing_path = next(iter(sorted(weeks_dir.glob(f"{day_s}_*.json"))), None)
    existing = json.loads(existing_path.read_text()) if existing_path else None
    if existing and source != "ufcstats.com":
        name = existing["event"]  # keep the official name once we have it
    # picks lock at the first bout's start; without a start time, conservatively 12:00 UTC the day before
    locks_at = start.tz_convert("UTC") if start is not None else (day - pd.Timedelta(hours=12)).tz_localize("UTC")
    if existing and existing.get("locks_at"):
        locks_at = min(locks_at, pd.Timestamp(existing["locks_at"]))
    if now >= locks_at:
        if existing:
            print(f"{existing['event']}: picks locked at {existing['locks_at']} - not changed")
        else:
            print(f"{name}: already started ({locks_at}) - too late to publish picks for it")
        return existing

    picks, _ = predict_card(card.reset_index(drop=True), name, day.to_pydatetime())
    slug = existing["slug"] if existing else _slug(day_s, name)
    week = {
        "event": name, "date": day_s, "slug": slug, "source": source,
        "published_at": existing["published_at"] if existing else now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "locks_at": locks_at.isoformat(timespec="seconds"),
        "bouts": _bouts(picks),
    }
    (weeks_dir / f"{slug}.json").write_text(json.dumps(week, indent=2))
    merged = tr.merge(rows, tr.picks_to_rows(picks, require_odds=False))
    rows[:] = merged
    pdf = site / "reports" / f"{slug}.pdf"
    report_mod.render(picks, name, day.to_pydatetime(), pdf, report_mod.record_from_tracking(rows))
    print(f"published {name} ({len(week['bouts'])} bouts, {sum(1 for b in week['bouts'] if b['bet'])} value bets)")
    return week


# ============================================================================ rendering helpers

def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def pct(p, digits=0) -> str:
    return "–" if p is None else f"{p * 100:.{digits}f}%"


def tier(p: float) -> tuple[str, str]:
    label, _ = report_mod.tier(p)
    return label, {"STRONG": "strong", "LEAN": "lean", "TOSS-UP": "toss"}[label]


def _octagon(r, c=20):
    import math
    return " ".join(f"{c + r * math.cos(math.radians(22.5 + 45 * k)):.2f},{c + r * math.sin(math.radians(22.5 + 45 * k)):.2f}"
                    for k in range(8))


LOGO = ('<svg class="logo" viewBox="0 0 40 40" aria-hidden="true"><polygon points="{o}" fill="#C8102E"/>'
        '<polygon points="{i}" fill="#17140F"/><text x="20" y="25.6" text-anchor="middle" font-size="15" '
        'font-weight="800" fill="#fff" font-family="Barlow Condensed,Arial Narrow,Helvetica,sans-serif">IQ</text></svg>'
        ).format(o=_octagon(19.5), i=_octagon(15.5))

# one 24px stroke icon set, drawn for the site (fight night, not dashboard)
ICONS = {
    "glove": '<path d="M6.5 12V8a5 5 0 0 1 5-5H14a5 5 0 0 1 5 5v4.5a4.5 4.5 0 0 1-4.5 4.5h-4A4 4 0 0 1 6.5 13z"/>'
             '<path d="M6.5 12.5H10a2.2 2.2 0 0 0 0-4.4H6.5"/><path d="M8.5 17v4h8v-4"/>',
    "belt": '<path d="M2 9.5h4.5M2 14.5h4.5M17.5 9.5H22M17.5 14.5H22"/><path d="M8.5 6h7l3 6-3 6h-7l-3-6z"/>'
            '<circle cx="12" cy="12" r="2.2"/>',
    "flame": '<path d="M12 3c.6 3.4 5 5.6 5 10.4a5 5 0 0 1-10 0c0-2.6 1.6-4.3 2.6-5.7.4 1.7 1.2 2.7 2.2 3.2.7-2.4.8-5 .2-7.9z"/>',
    "bolt": '<path d="M13.5 2 4.5 13.5h6.5L10 22l9-11.5h-6.5z"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
    "lock": '<rect x="5" y="11" width="14" height="9.5" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7"/>',
    "data": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>',
    "model": '<path d="M4 18 9 12l4 3 7-8"/><path d="M15 7h5v5"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "doc": '<path d="M6 3h8.5L19 7.5V21H6z"/><path d="M14 3v5h5M9 13h7M9 17h7"/>',
    "trophy": '<path d="M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M7 6H4v1a3 3 0 0 0 3 3M17 6h3v1a3 3 0 0 1-3 3M12 14v4M8 21h8M9.5 18h5"/>',
    "octagon": '<polygon points="8.3,3 15.7,3 21,8.3 21,15.7 15.7,21 8.3,21 3,15.7 3,8.3"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01" stroke-width="3"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M2 12h2.5M19.5 12H22M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>',
}


def icon(name: str, cls: str = "") -> str:
    return f'<span class="ico{" " + cls if cls else ""}"><svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg></span>'


def _v(text: str) -> str:
    """Short content hash for asset URLs, so browsers never keep an old stylesheet after an update."""
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


NAV = (("week", "index.html", "This week"), ("record", "record.html", "Record"),
       ("search", "search.html", "Past fights"), ("model", "model.html", "How it works"))
FONTS = ("https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,600;0,700;0,800;1,800"
         "&family=Barlow:wght@400;500;600;700&display=swap")


def layout(title: str, body: str, active: str, depth: int = 0, updated: str | None = None,
           script: str | None = None) -> str:
    up = "../" * depth
    links = []
    for key, href, label in NAV:
        on = ' class="on" aria-current="page"' if key == active else ""
        links.append(f'<a href="{up}{href}"{on}>{label}</a>')
    nav = "".join(links)
    extra = f'<script src="{up}assets/{script}?v={_v(SEARCH_JS)}"></script>' if script else ""
    stamp = (f'Updated <time datetime="{esc(updated)}">{esc(updated.replace("T", " ")[:16])} UTC</time>. '
             if updated else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · UFC FightIQ</title>
<meta name="description" content="UFC FightIQ: free, fully transparent UFC fight predictions, value bets and a public track record.">
<meta name="theme-color" content="#f5f1e8">
<link rel="icon" href="{up}assets/logo.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{up}assets/style.css?v={_v(CSS)}">
<script>try{{var t=localStorage.getItem("fightiq-theme");if(t)document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script>
</head>
<body>
<header class="top">
  <div class="wrap top-in">
    <a class="brand" href="{up}index.html" aria-label="FightIQ home">{LOGO}<span class="word"><small>UFC predictions</small>FIGHT<b>IQ</b></span></a>
    <nav aria-label="Main">{nav}</nav>
    <button class="theme" type="button" aria-label="Switch light or dark mode">{icon("moon", "moon")}{icon("sun", "sun")}</button>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="foot">
  <div class="wrap foot-in">
    <div class="foot-brand">{LOGO}<p><b>FightIQ is free and fully transparent.</b> Every pick is published before the fight,
    locked at the first bout and kept in the <a href="{REPO_URL}">public code repository</a>.</p></div>
    <p class="foot-small">{stamp}<span class="age">18+</span> Not financial advice. Bet responsibly. Gambling Help:
    <a href="https://www.gamblinghelponline.org.au">gamblinghelponline.org.au</a> · 1800 858 858</p>
  </div>
</footer>
<script src="{up}assets/app.js?v={_v(JS)}"></script>{extra}
</body>
</html>
"""


def hero(kicker: str, title: str, lede: str, side: str = "", ico: str = "octagon") -> str:
    side_html = f'<div class="hero-side">{side}</div>' if side else ""
    return (f'<section class="hero"><div><p class="kicker">{icon(ico)}{esc(kicker)}</p><h1>{esc(title)}</h1>'
            f'<p class="lede">{lede}</p></div>{side_html}</section>')


def kpis(items: list[tuple]) -> str:
    """A scoreboard strip: (label, value, sub[, css class[, icon]])."""
    cells = []
    for item in items:
        label, value, sub = item[:3]
        cls = f" {item[3]}" if len(item) > 3 and item[3] else ""
        ico = icon(item[4]) if len(item) > 4 and item[4] else ""
        cells.append(f'<div class="cell"><span class="lbl">{ico}{esc(label)}</span><strong class="val{cls}">{value}</strong>'
                     f'<span class="sub">{sub}</span></div>')
    return f'<section class="board">{"".join(cells)}</section>'


# ============================================================================ week view

def results_by_fight(rows: list[list]) -> dict:
    out = {}
    for r in rows:
        k = tr.row_key(r)
        if k:
            out[k] = r
    return out


def _headline(event: str) -> tuple[str, list[str]]:
    """'UFC 332: Silva vs. Wang' -> ('UFC 332', ['Silva', 'Wang'])."""
    prefix, sep, main = event.partition(":")
    if not sep:
        prefix, main = "", event
    return prefix.strip(), [n.strip() for n in re.split(r"\s+vs\.?\s+", main.strip(), maxsplit=1, flags=re.I)]


WATERMARK = ('<svg class="watermark" viewBox="0 0 40 40" aria-hidden="true"><polygon points="{o}"/>'
             '<polygon points="{i}"/></svg>').format(o=_octagon(19), i=_octagon(13.5))


def week_body(week: dict, rows: list[list], depth: int) -> str:
    up = "../" * depth
    bouts = week["bouts"]
    res = results_by_fight(rows)
    day = date.fromisoformat(week["date"])
    now = datetime.now(timezone.utc)
    locked = now >= datetime.fromisoformat(week["locks_at"])

    def result(b, side):
        r = res.get((day, frozenset(odds_mod._clean(n) for n in (b["pick"], b["opponent"]))))
        if not r:
            return None
        return r[tr.PICK_RES] if side == "pick" else r[tr.BET_RES]

    has_odds = any(b["odds"] is not None for b in bouts)
    bets = sorted((b for b in bouts if b["bet"]), key=lambda b: -(b["edge"] or 0))
    lock = max(bouts, key=lambda b: b["p"])
    dogs = [b for b in bouts if b["odds"] and b["opp_odds"] and b["odds"] > b["opp_odds"]]
    upset = max(dogs, key=lambda b: b["p"]) if dogs else None
    status = (f'<span class="status locked">{icon("lock")}Picks locked</span>' if locked else
              f'<span class="status live"><span class="pulse"></span>Live · locks <time datetime="{esc(week["locks_at"])}">'
              f'{esc(week["locks_at"].replace("T", " ")[:16])} UTC</time></span>')
    prefix, names = _headline(week["event"])
    title = (f'<span>{esc(names[0])}</span><i>vs</i><span>{esc(names[1])}</span>' if len(names) == 2
             else f'<span>{esc(names[0])}</span>')
    kicker = f'{esc(prefix or "UFC")} · {day:%A %d %B %Y}'
    head = f"""
<section class="poster">
  {WATERMARK}
  <div class="poster-copy">
    <p class="kicker">{icon("calendar")}{kicker}</p>
    <h1 class="bout" aria-label="{esc(week["event"])}">{title}</h1>
    <p class="meta">Card from {esc(week["source"])} · first published
    <time datetime="{esc(week["published_at"])}">{esc(week["published_at"][:10])}</time></p>
    {f'<p class="result-line">{icon("trophy")}<span>{results_line(week, rows)}</span></p>' if locked else ""}
  </div>
  <div class="poster-side">{status}<a class="btn" href="{up}reports/{esc(week["slug"])}.pdf">{icon("doc")}PDF report</a></div>
</section>"""
    tiles = kpis([
        ("Fights", str(len(bouts)), "on the card", "", "glove"),
        ("Value bets", str(len(bets)) if has_odds else "–", "below" if bets else ("none this week" if has_odds else "no odds yet"),
         "accent" if bets else "", "target"),
        ("Lock of the week", esc(lock["pick"]), f'{pct(lock["p"])} win chance', "", "flame"),
        ("Upset alert", esc(upset["pick"]) if upset else ("None" if has_odds else "–"),
         f'underdog @ {upset["odds"]:.2f} · {pct(upset["p"])}' if upset else ("model backs every favourite" if has_odds else "needs odds"),
         "accent" if upset else "", "bolt"),
    ])
    if bets:
        slips = "".join(_bet_card(b, result(b, "bet")) for b in bets)
        total = sum(b["stake"] or 0 for b in bets)
        bets_html = (f'<h2>{icon("target")}Value bets<span class="h2-note">{len(bets)} bets · {total:.1f}u staked · '
                     f'1u = 1% of bankroll</span></h2><div class="slips">{slips}</div>')
    else:
        msg = ("No value bets this week: the model agrees with the bookies on every fight." if has_odds
               else "Odds aren't posted yet. Value bets appear once the bookmakers price the card.")
        bets_html = f'<h2>{icon("target")}Value bets</h2><div class="empty">{msg}</div>'
    rows_html = "".join(_pred_row(b, result(b, "pick")) for b in sorted(bouts, key=lambda b: -b["p"]))
    legend = ('<span class="legend"><span class="tier strong">Strong 70%+</span><span class="tier lean">Lean 60–70%</span>'
              '<span class="tier toss">Toss-up &lt;60%</span></span>')
    note = ("Win chance = FightIQ model blended with the best available bookmaker odds." if has_odds
            else "Win chance = FightIQ stats model (odds not posted yet).")
    return f"""{head}{tiles}
<section>{bets_html}</section>
<section>
  <h2>{icon("glove")}Fight by fight {legend}</h2>
  <div class="tape" role="table" aria-label="FightIQ picks for every fight">
    <div class="bout-row tape-head" role="row"><span role="columnheader">FightIQ pick</span><span role="columnheader">Win chance</span>
    <span role="columnheader">Opponent</span><span role="columnheader">Call</span></div>
    {rows_html}
  </div>
  <p class="note">{note} Sorted by confidence. Full numbers: <a href="{up}data/weeks/{esc(week["slug"])}.json">JSON</a>.</p>
</section>"""


RESULT_TITLES = {"W": "Won", "L": "Lost", "D": "Draw", "NC": "No contest", "Cancelled": "Cancelled"}


def card_results(week: dict, rows: list[list]) -> dict:
    """W-L for the card's picks and its value bets (units at the posted stakes), from the settled tracking rows."""
    res = results_by_fight(rows)
    day = date.fromisoformat(week["date"])
    out = {"wins": 0, "losses": 0, "bet_wins": 0, "bet_losses": 0, "units": 0.0, "settled": 0}
    for b in week["bouts"]:
        r = res.get(_bout_key(day, b["pick"], b["opponent"]))
        if not r or r[tr.PICK_RES] in (None, ""):
            continue
        out["settled"] += 1
        out["wins"] += r[tr.PICK_RES] == "W"
        out["losses"] += r[tr.PICK_RES] == "L"
        if r[tr.BET_ON] and r[tr.STAKE] is not None and r[tr.BET_ODDS] is not None:
            if r[tr.BET_RES] == "W":
                out["bet_wins"] += 1
                out["units"] += r[tr.STAKE] * (r[tr.BET_ODDS] - 1)
            elif r[tr.BET_RES] == "L":
                out["bet_losses"] += 1
                out["units"] -= r[tr.STAKE]
    return out


def results_line(week: dict, rows: list[list]) -> str:
    c = card_results(week, rows)
    if not c["settled"]:
        return "Results are added once the official fight stats are published, usually within a day."
    bets = c["bet_wins"] + c["bet_losses"]
    line = f'FightIQ went <b>{c["wins"]}-{c["losses"]}</b> on its picks'
    if bets:
        cls = "good" if c["units"] > 0 else "bad" if c["units"] < 0 else ""
        line += f' · value bets <b>{c["bet_wins"]}-{c["bet_losses"]}</b>, <b class="{cls}">{c["units"]:+.2f}u</b>'
    return line + "."


def _badge(result: str | None) -> str:
    if not result:
        return '<span class="res pending" title="Result not in yet">–</span>'
    cls = {"W": "win", "L": "loss"}.get(result, "void")
    return f'<span class="res {cls}" title="{esc(RESULT_TITLES.get(result, result))}">{esc(result)}</span>'


def _pred_row(b: dict, result: str | None) -> str:
    label, cls = tier(b["p"])
    tags = []
    if b["debut"]:
        tags.append('<span class="tag">Debut</span>')
    if b["bet"] == b["pick"]:
        tags.append('<span class="tag accent">Value bet</span>')
    elif b["bet"]:
        tags.append(f'<span class="tag accent">Value: {esc(b["bet"].split()[-1])} @ {b["bet_odds"]:.2f}</span>')
    odds = f'odds {b["odds"]:.2f}' if b["odds"] else "no odds yet"
    opp_odds = f'odds {b["opp_odds"]:.2f}' if b["opp_odds"] else ""
    q = 1 - b["p"]
    opp_text = pct(q) if q >= 0.12 else ""
    call = "Toss-up" if label == "TOSS-UP" else label.title()
    tip = (f'{esc(b["pick"])}: {pct(b["p"], 1)}'
           + (f' · model {pct(b["p_model"], 1)} · bookies {pct(b["p_market"], 1)}' if b["p_market"] is not None else ""))
    return f"""<div class="bout-row" role="row">
  <div class="corner pick" role="cell"><b>{esc(b["pick"])}</b><small>{odds}{"".join(tags)}</small></div>
  <div class="split" role="cell" data-tip="{tip}"><span class="seg {cls}" style="width:{b["p"] * 100:.1f}%">{pct(b["p"])}</span><span class="seg opp">{opp_text}</span></div>
  <div class="corner opp" role="cell"><b>{esc(b["opponent"])}</b><small>{opp_odds}</small></div>
  <div class="call" role="cell"><span class="tier {cls}">{call}</span>{_badge(result)}</div>
</div>"""


def _bet_card(b: dict, result: str | None) -> str:
    opp = b["opponent"] if b["bet"] == b["pick"] else b["pick"]
    corner = _badge(result) if result else '<span class="slip-id">FightIQ</span>'
    return f"""<article class="slip">
  <div class="slip-top"><span class="stamp">{icon("target")}Back</span>{corner}</div>
  <div class="slip-main"><div><h3>{esc(b["bet"])}</h3><small>to beat {esc(opp)}</small></div>
  <div class="odds"><span>Odds</span><strong>{b["bet_odds"]:.2f}</strong></div></div>
  <div class="perf" aria-hidden="true"></div>
  <dl class="slip-stats"><div><dt>Win chance</dt><dd>{pct(b["bet_p"])}</dd></div>
  <div><dt>Edge</dt><dd class="good">+{b["edge"] * 100:.1f}%</dd></div><div><dt>Stake</dt><dd>{b["stake"]:.1f}u</dd></div></dl>
</article>"""


# ============================================================================ record view

def record_stats(rows: list[list]) -> dict:
    picks = [r for r in rows if r[tr.PICK_RES] in ("W", "L")]
    bets = [r for r in rows if r[tr.BET_ON] and r[tr.BET_RES] in ("W", "L", "D", "NC")
            and r[tr.BET_ODDS] is not None and r[tr.STAKE] is not None]

    def pnl(r):
        if r[tr.BET_RES] == "W":
            return r[tr.STAKE] * (r[tr.BET_ODDS] - 1)
        return -r[tr.STAKE] if r[tr.BET_RES] == "L" else 0.0

    bets = sorted(bets, key=lambda r: tr._to_date(r[tr.DATE]))
    staked = sum(r[tr.STAKE] for r in bets)
    profit = sum(pnl(r) for r in bets)
    cum, path = 0.0, []
    for r in bets:
        cum += pnl(r)
        path.append((r, pnl(r), cum))
    wins = sum(r[tr.PICK_RES] == "W" for r in picks)
    return {"wins": wins, "losses": len(picks) - wins, "bets": len(bets),
            "bet_wins": sum(r[tr.BET_RES] == "W" for r in bets), "staked": staked, "profit": profit,
            "roi": profit / staked if staked else None, "path": path, "pnl": pnl}


def cumulative_chart(path: list) -> str:
    """Cumulative units over settled bets: one 2px line, zero baseline, hover tooltip per bet."""
    if len(path) < 2:
        return '<div class="empty">The profit chart starts once a couple of bets have been settled.</div>'
    W, H, L, R, T, B = 720, 260, 44, 64, 16, 28
    ys = [0.0] + [c for _, _, c in path]
    lo, hi = min(ys), max(ys)
    pad = max(1.0, (hi - lo) * 0.1)
    lo, hi = lo - pad, hi + pad
    x = lambda i: L + (W - L - R) * i / (len(ys) - 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    step = _nice_step(hi - lo)
    grid = ""
    v = np.ceil(lo / step) * step
    while v <= hi:
        zero = abs(v) < 1e-9
        label = "0u" if zero else f"{v:+g}u"
        cls = "grid zero" if zero else "grid"
        grid += (f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="{cls}"/>'
                 f'<text x="{L - 8}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{label}</text>')
        v += step
    pts = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(ys))
    hits = ""
    for i, (r, p, c) in enumerate(path, start=1):
        d = tr._to_date(r[tr.DATE])
        tip = esc(f"{d:%d %b %Y} · {r[tr.BET_ON]} @ {r[tr.BET_ODDS]:.2f} · {r[tr.BET_RES]} {p:+.2f}u · total {c:+.2f}u")
        w = (W - L - R) / (len(ys) - 1)
        hits += (f'<rect x="{x(i) - w / 2:.1f}" y="{T}" width="{w:.1f}" height="{H - T - B}" class="hit" data-tip="{tip}"'
                 f' data-x="{x(i):.1f}" data-y="{y(c):.1f}"/>')
    last = ys[-1]
    return f"""<figure class="chart">
<figcaption>Cumulative profit, units (1u = 1% of bankroll)</figcaption>
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Cumulative profit {last:+.1f} units over {len(path)} bets">
{grid}<polyline points="{pts}" class="line"/>
<circle cx="{x(len(ys) - 1):.1f}" cy="{y(last):.1f}" r="4.5" class="dot"/>
<text x="{x(len(ys) - 1) + 9:.1f}" y="{y(last) + 4:.1f}" class="label">{last:+.1f}u</text>
<text x="{L}" y="{H - 8}" class="axis">bet 1</text><text x="{W - R}" y="{H - 8}" class="axis" text-anchor="end">bet {len(path)}</text>
<line class="cross" x1="0" x2="0" y1="{T}" y2="{H - B}"/><circle class="cross-dot" r="4.5" cx="-10" cy="-10"/>
{hits}
</svg></figure>"""


def _nice_step(span: float) -> float:
    raw = span / 4
    mag = 10 ** np.floor(np.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def record_body(rows: list[list], weeks: list[dict]) -> str:
    s = record_stats(rows)
    since = min((tr._to_date(r[tr.DATE]) for r in rows), default=None)
    tiles = kpis([
        ("Picks", f'{s["wins"]}-{s["losses"]}' if s["wins"] + s["losses"] else "–", "winners picked", "", "glove"),
        ("Hit rate", pct(s["wins"] / (s["wins"] + s["losses"])) if s["wins"] + s["losses"] else "–", "of settled picks",
         "", "check"),
        ("Value bets", str(s["bets"]), f'{s["bet_wins"]} won' if s["bets"] else "none settled yet", "", "target"),
        ("Profit", f'{s["profit"]:+.1f}u' if s["bets"] else "–", f'{s["staked"]:.1f}u staked' if s["bets"] else "",
         "good" if s["profit"] > 0 else ("bad" if s["profit"] < 0 else ""), "trophy"),
        ("ROI", pct(s["roi"], 1) if s["roi"] is not None else "–", f"since {since:%d %b %Y}" if since else "",
         "good" if (s["roi"] or 0) > 0 else ("bad" if (s["roi"] or 0) < 0 else ""), "model"),
    ])
    by_event = {}
    for r in rows:
        by_event.setdefault((tr._to_date(r[tr.DATE]), r[tr.EVENT]), []).append(r)
    slug_for = {(w["date"], w["event"]): w["slug"] for w in weeks}
    ev_rows = ""
    for (d, ev), rs in sorted(by_event.items(), key=lambda kv: kv[0][0], reverse=True):
        w = sum(r[tr.PICK_RES] == "W" for r in rs)
        l = sum(r[tr.PICK_RES] == "L" for r in rs)
        bets = [r for r in rs if r[tr.BET_ON] and r[tr.BET_RES] in ("W", "L", "D", "NC")]
        profit = sum(s["pnl"](r) for r in bets)
        slug = slug_for.get((d.isoformat(), ev))
        name = f'<a href="weeks/{esc(slug)}.html">{esc(ev)}</a>' if slug else esc(ev)
        ev_rows += (f"<tr><td>{d:%d %b %Y}</td><td>{name}</td><td>{w}-{l}</td><td>{len(bets)}</td>"
                    f'<td class="{"good" if profit > 0 else "bad" if profit < 0 else ""}">{profit:+.2f}u</td></tr>')
    log = ""
    for r in sorted(rows, key=lambda r: tr._to_date(r[tr.DATE]), reverse=True):
        d = tr._to_date(r[tr.DATE])
        bet = r[tr.BET_ON]
        settled_bet = bet and r[tr.BET_RES] in ("W", "L", "D", "NC")
        p = s["pnl"](r) if settled_bet else None
        log += (f"<tr><td>{d:%d %b}</td><td>{esc(r[tr.FIGHT])}</td><td><b>{esc(r[tr.PICK])}</b></td>"
                f"<td>{pct(r[tr.PROB])}</td><td>{_badge(r[tr.PICK_RES])}</td>"
                f"<td>{esc(bet) if bet else ''}</td><td>{f'{r[tr.BET_ODDS]:.2f}' if bet else ''}</td>"
                f"<td>{f'{r[tr.STAKE]:.1f}u' if bet else ''}</td>"
                f'<td class="{"good" if (p or 0) > 0 else "bad" if (p or 0) < 0 else ""}">{f"{p:+.2f}u" if p is not None else ""}</td></tr>')
    empty = '<tr><td colspan="9" class="muted">The record starts with the first card published on this site.</td></tr>'
    lede = ("Every pick and value bet since launch, settled from the official results. Nothing is ever removed or edited: "
            f'picks lock when the first fight starts, and the full history is in the <a href="{REPO_URL}/commits">public commit log</a>.')
    csv_btn = f'<a class="btn" href="data/tracking.csv">{icon("data")}CSV</a>'
    return f"""
{hero("Since launch", "Live record", lede, csv_btn, "trophy")}
{tiles}
<section><h2>{icon("model")}Profit over time</h2>{cumulative_chart(s["path"])}</section>
<section><h2>{icon("calendar")}By event</h2>
<div class="tablewrap"><table><thead><tr><th>Date</th><th>Event</th><th>Picks</th><th>Bets</th><th>Profit</th></tr></thead>
<tbody>{ev_rows or '<tr><td colspan="5" class="muted">No events yet.</td></tr>'}</tbody></table></div></section>
<section><h2>{icon("data")}Every pick</h2>
<div class="tablewrap"><table class="log"><thead><tr><th>Date</th><th>Fight</th><th>Pick</th><th>Win chance</th><th>Result</th>
<th>Bet</th><th>Odds</th><th>Stake</th><th>Profit</th></tr></thead><tbody>{log or empty}</tbody></table></div>
<p class="note">Units: 1u = 1% of bankroll. Stakes are a quarter of the Kelly amount, capped at 2u. D = draw, NC = no contest
(stake returned); cancelled bouts don't count.</p></section>"""


# ============================================================================ model view

def year_bars(per_year: list[dict]) -> str:
    """Profit per test year: one series, bars from a zero baseline, value labels at the bar ends."""
    W, H, L, R, T, B = 720, 240, 40, 12, 22, 30
    vals = [p["profit"] for p in per_year]
    lo, hi = min(0, min(vals)), max(0, max(vals))
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.12, hi + span * 0.15
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    band = (W - L - R) / len(per_year)
    bw = min(24, band * 0.6)
    out = f'<line x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="grid zero"/>'
    for i, p in enumerate(per_year):
        cx = L + band * (i + 0.5)
        y0, y1 = y(0), y(p["profit"])
        top, h = min(y0, y1), abs(y1 - y0)
        r = min(4, h)
        x0 = cx - bw / 2
        if p["profit"] >= 0:  # rounded at the top (data end), square at the baseline
            d = (f"M{x0:.1f},{y0:.1f} V{top + r:.1f} Q{x0:.1f},{top:.1f} {x0 + r:.1f},{top:.1f} H{x0 + bw - r:.1f} "
                 f"Q{x0 + bw:.1f},{top:.1f} {x0 + bw:.1f},{top + r:.1f} V{y0:.1f} Z")
            ly = top - 6
        else:
            bot = top + h
            d = (f"M{x0:.1f},{y0:.1f} V{bot - r:.1f} Q{x0:.1f},{bot:.1f} {x0 + r:.1f},{bot:.1f} H{x0 + bw - r:.1f} "
                 f"Q{x0 + bw:.1f},{bot:.1f} {x0 + bw:.1f},{bot - r:.1f} V{y0:.1f} Z")
            ly = bot + 13
        tip = esc(f'{p["year"]}: {p["bets"]} bets, {p["profit"]:+.1f}u, ROI {p["roi"] * 100:+.1f}%')
        out += (f'<path d="{d}" class="barfill" data-tip="{tip}"/>'
                f'<text x="{cx:.1f}" y="{ly:.1f}" class="label" text-anchor="middle">{p["profit"]:+.0f}</text>'
                f'<text x="{cx:.1f}" y="{H - 10}" class="axis" text-anchor="middle">{p["year"]}</text>')
    return (f'<figure class="chart"><figcaption>Backtest profit by year, units (FightIQ bet rule, flat 1u per bet)</figcaption>'
            f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Backtest profit by year">{out}</svg></figure>')


def model_body(bt: dict, model_meta: dict) -> str:
    q = {r["model"]: r for r in bt["quality_odds"]}
    blend, market, stats = q["FightIQ blend"], q["Bookmaker odds"], q["FightIQ stats model"]
    rule = bt["rule"]
    feats = sorted(bt["features"], key=lambda f: -abs(f["impact"]))
    max_imp = max(abs(f["impact"]) for f in feats)
    feat_rows = ""
    for f in feats:
        text, higher_good = FEATURE_TEXT.get(f["feature"], (f["feature"], f["coef"] > 0))
        direction = "helps" if f["coef"] > 0 else "hurts"
        swing = (1 / (1 + np.exp(-abs(f["impact"]))) - 0.5) * 100
        feat_rows += (f'<tr><td>{esc(text)}</td><td>{"Higher" if f["feature"] != "debut" else "Debuting"} {direction}</td>'
                      f'<td><span class="mini"><span style="width:{abs(f["impact"]) / max_imp * 100:.0f}%"></span></span>'
                      f' ±{swing:.1f} pts</td></tr>')
    q_rows = "".join(f'<tr><td>{esc(r["model"])}</td><td>{r["n"]:,}</td><td>{pct(r["accuracy"], 1)}</td>'
                     f'<td>{r["log_loss"]:.4f}</td><td>{r["brier"]:.4f}</td></tr>' for r in bt["quality_odds"])
    s_rows = ""
    for r in bt["strategies"]:
        hl = ' class="hl"' if r["strategy"] == "FightIQ blend" and r["threshold"] == rule["threshold"] else ""
        edge = f' · edge &gt; {r["threshold"] * 100:.0f}%' if r["threshold"] is not None else ""
        roi_cls = "good" if r["roi"] > 0 else "bad"
        s_rows += (f'<tr{hl}><td>{esc(r["strategy"])}{edge}</td><td>{r["bets"]:,}</td><td>{r["profit"]:+.1f}u</td>'
                   f'<td class="{roi_cls}">{pct(r["roi"], 1)}</td></tr>')
    cal_rows = "".join(f'<tr><td>{esc(c["predicted"])}</td><td>{c["fights"]:,}</td><td>{pct(c["avg_predicted"], 1)}</td>'
                       f'<td>{pct(c["actual_win_rate"], 1)}</td></tr>' for c in bt["calibration"])
    band_rows = "".join(f'<tr><td>{esc(b["odds"])}</td><td>{b["bets"]}</td><td class="{"good" if (b["roi"] or 0) > 0 else "bad"}">'
                        f'{pct(b["roi"], 1) if b["roi"] is not None else "–"}</td></tr>' for b in bt["odds_bands"])
    y0, y1 = bt["test_years"]
    trained = bt["trained"]
    steps = [
        ("data", "Real fight data", "Round-by-round stats for every UFC bout since UFC 1, from ufcstats.com, refreshed daily."),
        ("clock", "Only what was known", "Each fighter's numbers are rebuilt as they stood the day before the fight. No hindsight."),
        ("glove", "Compare the fighters", "A logistic regression (plain statistics, not AI) turns 12 differences between the two "
                                          "fighters into a win chance."),
        ("target", "Blend with the odds", "The model adjusts the bookmakers' price. A bet needs a 5%+ edge; stakes are small and capped."),
    ]
    step_html = "".join(f'<div class="round"><span class="rnum">Round {n}</span>{icon(i)}<h3>{esc(t)}</h3><p>{esc(d)}</p></div>'
                        for n, (i, t, d) in enumerate(steps, start=1))
    top = hero("The method", "How FightIQ works", "Everything is public: the method, the code, the backtest and the live "
               "results. Here is the short version, in four rounds.", "", "octagon")
    return f"""
{top}
<section class="rounds">{step_html}</section>

<section><h2>{icon("trophy")}The headline numbers</h2>
{kpis([("FightIQ picks", pct(blend["accuracy"], 1), f"winners, {y0}–{y1} backtest", "good", "glove"),
       ("Bookmaker favourites", pct(market["accuracy"], 1), "same fights", "", "data"),
       ("Stats model alone", pct(stats["accuracy"], 1), "without the odds", "", "model"),
       ("Backtest bets", f'{rule["roi"] * 100:+.1f}% ROI', f'{rule["bets"]} bets, {rule["profit"]:+.0f}u', "", "target")])}
<p>On its own, a stats model doesn't beat the betting market: bookmakers know a lot. FightIQ's edge comes from combining the two.
It starts from the market's price and nudges it using the stats, which beat the market's own probabilities on
{blend["n"]:,} historical fights (log loss {blend["log_loss"]:.4f} vs {market["log_loss"]:.4f}).</p></section>

<section><h2>{icon("glove")}What the model looks at</h2>
<p class="note">All numbers are fighter A minus fighter B. "Typical effect" is how much a normal-sized difference moves the win chance.</p>
<div class="tablewrap"><table><thead><tr><th>Factor</th><th>Direction</th><th>Typical effect</th></tr></thead><tbody>{feat_rows}</tbody></table></div>
<p class="note">Model trained on {trained.get("trained_fights", 0):,} UFC bouts ({esc(trained.get("trained_from"))} to {esc(trained.get("trained_to"))}).
Both fighters are always considered from both corners, so the order they're listed in can't change a prediction.
Blend: logit(p) = {bt["blend"]["market_logit"]:.3f} × logit(bookmaker p) + {bt["blend"]["model_score"]:.3f} × model score.</p></section>

<section><h2>{icon("target")}Bet rules</h2>
<ul class="rules">
<li><b>Edge 5%+</b>: a bet is only suggested when the best available odds beat FightIQ's win chance by 5% or more.</li>
<li><b>Small, capped stakes</b>: a quarter of the Kelly amount, never more than 2u (1u = 1% of bankroll).</li>
<li><b>No debut bets</b>: there are no UFC stats for debutants, and the backtest showed no edge on those fights.</li>
<li><b>Locked at fight time</b>: picks are final when the first bout starts and are never edited.</li>
</ul></section>

<section><h2>{icon("model")}Backtest: {y0}–{y1}, trained only on earlier years</h2>
{year_bars(bt["per_year"])}
<div class="grid2">
<div class="tablewrap"><table><thead><tr><th>Predictions</th><th>Fights</th><th>Accuracy</th><th>Log loss</th><th>Brier</th></tr></thead><tbody>{q_rows}</tbody></table></div>
<div class="tablewrap"><table><thead><tr><th>Betting strategy</th><th>Bets</th><th>Profit</th><th>ROI</th></tr></thead><tbody>{s_rows}</tbody></table></div>
<div class="tablewrap"><table><thead><tr><th>FightIQ said</th><th>Fights</th><th>Average</th><th>Actually won</th></tr></thead><tbody>{cal_rows}</tbody></table></div>
<div class="tablewrap"><table><thead><tr><th>Odds of the bet</th><th>Bets</th><th>ROI</th></tr></thead><tbody>{band_rows}</tbody></table></div>
</div>
<p class="note">Lower log loss and Brier scores are better. Bet rule: 95% range for ROI {pct(rule["roi_95ci"][0], 1)} to {pct(rule["roi_95ci"][1], 1)}
(resampling whole cards). Full report: <a href="{REPO_URL}/blob/main/reports/evaluation.md">evaluation.md</a>.
Look up any fight in this backtest on <a href="search.html">Past fights</a>.</p></section>

<section><h2>{icon("bolt")}Honest caveats</h2>
<ul class="rules">
<li>The historical odds come from a public dataset that doesn't say whether they're opening or closing prices. If they're
openers, the real edge is smaller than the backtest shows. The live record on this site is the real test.</li>
<li>Bets are rare (a handful per card) and fights are noisy, so even a genuine edge can lose for months.</li>
<li>The model knows nothing about injuries, weight cuts, short-notice changes or camp news.</li>
<li>An earlier version of this model claimed 74.5% accuracy; that was an error (it used fighters' future stats). Its real
live record was 62%. <a href="{REPO_URL}/blob/main/reports/original_model_review.md">What went wrong and how it was fixed</a>.</li>
</ul></section>

<section><h2>{icon("data")}Data and code</h2>
<ul class="rules">
<li>Fight stats: <a href="http://ufcstats.com">ufcstats.com</a>, via the daily public scrape at <a href="https://github.com/Greco1899/scrape_ufc_stats">Greco1899/scrape_ufc_stats</a>.</li>
<li>Live odds: <a href="https://the-odds-api.com">The Odds API</a>, best price across Australian bookmakers.</li>
<li>Historical odds for the backtest: <a href="https://github.com/shortlikeafox/ultimate_ufc_dataset">ultimate_ufc_dataset</a>.</li>
<li>All code, picks and results: <a href="{REPO_URL}">{REPO_URL.replace("https://", "")}</a>.</li>
</ul></section>"""


# ============================================================================ past fights

EXAMPLES = ("Jon Jones", "Conor McGregor", "Amanda Nunes", "Israel Adesanya", "Max Holloway", "UFC 300")


def search_body(hist: dict | None) -> str:
    """Fight finder: every past fight and event; assets/search.js does the searching in the browser."""
    if not hist:
        return hero("Fight finder", "Past fights", "Search every past UFC fight and see who FightIQ would have picked.",
                    "", "search") + '<section><div class="empty">The fight finder appears after the next daily update.</div></section>'
    first = hist["events"][0][0][:4]
    to = date.fromisoformat(hist["fights_to"])
    lede = (f"{len(hist['fights']):,} UFC fights and {len(hist['events']):,} events, {first} to {to:%B %Y}. See who FightIQ "
            "would have picked and how every card went. Each pick uses only what was known before the fight: every year "
            "is predicted by a model trained only on earlier years. No hindsight.")
    side = f'<a class="btn" href="data/{HISTORY_FILE}">{icon("data")}JSON</a>'
    chips = "".join(f'<button class="chip" type="button">{esc(x)}</button>' for x in EXAMPLES)
    return hero("Fight finder", "Past fights & events", lede, side, "search") + f"""
<section id="finder" data-src="data/{HISTORY_FILE}">
  <div class="tabs" role="tablist" aria-label="View">
    <button type="button" role="tab" class="tab" data-view="fights" aria-selected="true">{icon("glove")}Fights</button>
    <button type="button" role="tab" class="tab" data-view="events" aria-selected="false">{icon("calendar")}Events</button>
  </div>
  <div class="finder-bar">
    <div class="search">{icon("search")}<label class="vh" for="q">Search a fighter or event</label>
    <input id="q" type="search" placeholder="Search a fighter or event, e.g. Jon Jones" autocomplete="off" spellcheck="false"></div>
    <label class="vh" for="show">Show</label>
    <select id="show">
      <option value="all">All fights</option><option value="bets">FightIQ bets only</option>
      <option value="upsets">Upsets FightIQ called</option><option value="misses">Picks it got wrong</option>
    </select>
    <label class="vh" for="sort">Sort events</label>
    <select id="sort" hidden>
      <option value="new">Newest first</option><option value="best">Best FightIQ cards</option>
      <option value="worst">Worst FightIQ cards</option>
    </select>
  </div>
  <div class="chips"><span>Try</span>{chips}</div>
  <p id="summary" class="summary" aria-live="polite">Loading fights…</p>
  <div class="list">
    <div class="frow fhead" id="head-fights"><span>Fight</span><span>FightIQ pick</span><span>Winner</span><span>Right?</span></div>
    <div class="erow ehead" id="head-events" hidden><span>Event</span><span>Picks right</span><span>Hit rate</span>
    <span>Avg confidence</span><span>Bets</span></div>
    <div id="results"></div>
  </div>
  <button id="more" class="btn more" type="button" hidden>Show more</button>
  <noscript><div class="empty">The fight finder needs JavaScript. All the data is in the JSON file above.</div></noscript>
</section>
<p class="note">Win chance = FightIQ's blend of the stats model and the bookmakers' odds where historical odds exist (2015 on),
otherwise the stats model alone. Hit rate = share of a card's fights (with a winner) where FightIQ's pick won; avg confidence =
FightIQ's average win chance for its picks on that card. Bets follow the site's rule (edge over 5%, no debuts) at a flat 1 unit,
exactly as in the <a href="model.html">backtest</a>. Before 2006 there were few earlier UFC fights to learn from, so those picks
are rough.</p>"""


# ============================================================================ build

def build(state: Path = STATE, site: Path = SITE) -> Path:
    weeks_dir, tracking_csv = _paths(state)
    rows = read_tracking(tracking_csv)
    weeks = load_weeks(state)
    bt = json.loads((config.ROOT / "reports" / "backtest.json").read_text())
    meta = json.loads(config.MODEL_PATH.read_text()).get("meta", {})
    site.mkdir(parents=True, exist_ok=True)
    (site / "assets").mkdir(exist_ok=True)
    (site / "weeks").mkdir(exist_ok=True)
    (site / "data" / "weeks").mkdir(parents=True, exist_ok=True)
    (site / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    (site / "assets" / "app.js").write_text(JS, encoding="utf-8")
    (site / "assets" / "logo.svg").write_text(LOGO.replace('class="logo" ', 'xmlns="http://www.w3.org/2000/svg" ')
                                              .replace("var(--red)", "#D20A0A"))
    (site / ".nojekyll").write_text("")
    if tracking_csv.exists():
        (site / "data" / "tracking.csv").write_text(tracking_csv.read_text())
    else:
        (site / "data" / "tracking.csv").write_text(",".join(tr.COLUMNS) + "\n")
    updated = max([w["updated_at"] for w in weeks], default=None)

    for w in weeks:
        (site / "data" / "weeks" / f'{w["slug"]}.json').write_text(json.dumps(w, indent=2))
        (site / "weeks" / f'{w["slug"]}.html').write_text(
            layout(w["event"], week_body(w, rows, depth=1), "record", depth=1, updated=w["updated_at"]))

    # the next card whose picks are still open; once every card has locked, the latest one (live or just finished).
    # The card before it is shown in the "Last card" strip with its result.
    now = datetime.now(timezone.utc)
    open_cards = [w for w in weeks if datetime.fromisoformat(w["locks_at"]) > now]
    current = open_cards[0] if open_cards else (weeks[-1] if weeks else None)
    if current:
        body = week_body(current, rows, depth=0)
        previous = [w for w in weeks if w["date"] < current["date"]]
        if previous:
            last = previous[-1]
            body = (f'<a class="lastcard" href="weeks/{esc(last["slug"])}.html"><span class="kicker">{icon("trophy")}Last card · '
                    f'{date.fromisoformat(last["date"]):%d %b %Y}</span><b>{esc(last["event"])}</b>'
                    f'<span class="lc-line">{results_line(last, rows)}</span><span class="lc-go">See every result '
                    f'{icon("arrow")}</span></a>') + body
    else:
        body = (hero("Between fight weeks", "Next card coming soon", "Picks for the next UFC card are published here as "
                     "soon as the card and odds are out (usually Thursday). Every pick is locked when the first fight starts.",
                     "", "calendar")
                + f'<section><h2>{icon("octagon")}Meanwhile</h2><p><a href="model.html">See how FightIQ works</a>, the '
                  f'<a href="record.html">live record</a>, or <a href="search.html">look up any past fight</a>.</p></section>')
    (site / "index.html").write_text(layout("This week", body, "week", updated=updated))
    (site / "record.html").write_text(layout("Record", record_body(rows, weeks), "record", updated=updated))
    (site / "model.html").write_text(layout("How it works", model_body(bt, meta), "model", updated=updated))

    hist_path = state / HISTORY_FILE
    hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else None
    if hist:
        (site / "data" / HISTORY_FILE).write_text(hist_path.read_text(encoding="utf-8"), encoding="utf-8")
    (site / "assets" / "search.js").write_text(SEARCH_JS, encoding="utf-8")
    (site / "search.html").write_text(layout("Past fights", search_body(hist), "search", updated=updated,
                                             script="search.js" if hist else None))
    return site / "index.html"


# ============================================================================ assets

CSS = r"""
:root{color-scheme:light;--paper:#f5f1e8;--card:#fffdf8;--card-2:#f1ece1;--ink:#17140f;--ink-2:#3b352c;--muted:#6e675a;
--line:#e3dccd;--line-2:#cdc3ae;--red:#c8102e;--red-ink:#a50d26;--red-soft:#f8e4e2;--blue:#1d4f91;--good:#1f7a3f;--bad:#b42318;
--strong:#1f7a3f;--lean:#b07d1f;--toss:#8c8578;--series:#1d4f91;--grid:#e8e2d5;--opp:#e8e1d2;
--display:"Barlow Condensed","Arial Narrow",Impact,sans-serif;--body:Barlow,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
:root[data-theme="dark"]{color-scheme:dark;--paper:#141311;--card:#1d1b18;--card-2:#25221e;--ink:#f3efe7;--ink-2:#d8d1c3;
--muted:#a69f91;--line:#33302a;--line-2:#4b463d;--red:#ef4056;--red-ink:#ff7385;--red-soft:#3a1b1f;--blue:#7aa5e6;--good:#55c27f;
--bad:#f2685d;--strong:#2f9e5b;--lean:#c4922f;--toss:#77705f;--series:#7aa5e6;--grid:#2c2924;--opp:#302d27}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}[hidden]{display:none!important}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 var(--body)}
a{color:var(--red-ink);text-underline-offset:2px}a:hover{color:var(--red)}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}
.ico{display:inline-flex;width:18px;height:18px;flex:none;color:var(--red)}
.ico svg{width:100%;height:100%;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.muted{color:var(--muted)}.small{font-size:13px}.good{color:var(--good)}.bad{color:var(--bad)}.accent{color:var(--red)}

.top{background:var(--card);box-shadow:0 1px 0 var(--line);position:sticky;top:0;z-index:5}
.top::before{content:"";display:block;height:5px;background:linear-gradient(90deg,var(--red) 0 50%,var(--blue) 50% 100%)}
.top-in{display:flex;align-items:center;gap:18px;min-height:66px}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink);margin-right:auto}
.brand:hover{color:var(--ink)}.logo{width:40px;height:40px;flex:none}
.word{font:800 30px/.9 var(--display);letter-spacing:.5px}.word b{color:var(--red)}
.word small{display:block;font:700 10px/1 var(--body);letter-spacing:2.6px;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
nav{display:flex;gap:2px}
nav a{font:700 17px/1 var(--display);letter-spacing:.7px;text-transform:uppercase;color:var(--ink-2);text-decoration:none;
padding:24px 12px 21px;border-bottom:3px solid transparent}
nav a:hover{color:var(--red)}nav a.on{color:var(--ink);border-bottom-color:var(--red)}
.theme{appearance:none;border:1.5px solid var(--line-2);background:var(--card);width:38px;height:38px;border-radius:50%;
display:grid;place-items:center;cursor:pointer;padding:0}
.theme .ico{color:var(--ink-2)}.theme .sun,[data-theme="dark"] .theme .moon{display:none}[data-theme="dark"] .theme .sun{display:inline-flex}

main.wrap{padding:30px 20px 56px}
section{margin:32px 0}
main.wrap > :first-child{margin-top:0}
h1{font:800 48px/.96 var(--display);letter-spacing:.2px;text-transform:uppercase;margin:8px 0 12px}
h2{display:flex;align-items:center;flex-wrap:wrap;gap:8px;font:800 25px/1.1 var(--display);text-transform:uppercase;letter-spacing:.5px;
margin:0 0 16px;padding-bottom:8px;border-bottom:2px solid var(--ink)}
h2 .ico{width:22px;height:22px}
h3{margin:0;font:700 22px/1.1 var(--display);letter-spacing:.2px}
.h2-note{font:500 14px/1.3 var(--body);text-transform:none;letter-spacing:0;color:var(--muted);margin-left:4px}
.kicker{display:flex;align-items:center;gap:8px;margin:0;font:700 15px/1.2 var(--display);letter-spacing:2px;text-transform:uppercase;color:var(--red-ink)}
.lede{margin:0;max-width:690px;color:var(--ink-2);font-size:17px}
.note{color:var(--muted);font-size:14px;margin:12px 0 0}

.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:16px 24px;flex-wrap:wrap;margin-top:6px;
padding-bottom:24px;border-bottom:1px solid var(--line)}
.hero-side,.poster-side{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--ink);color:var(--paper);border:0;padding:11px 17px 10px;
border-radius:999px;text-decoration:none;font:700 15px/1 var(--display);letter-spacing:.9px;text-transform:uppercase;cursor:pointer}
.btn .ico{color:inherit;width:16px;height:16px}.btn:hover{background:var(--red);color:#fff}

.poster{position:relative;overflow:hidden;background:var(--card);border:1px solid var(--line);border-radius:4px;
padding:30px 30px 26px;display:flex;justify-content:space-between;align-items:flex-end;gap:20px;flex-wrap:wrap}
.poster::before{content:"";position:absolute;left:0;right:0;top:0;height:6px;background:linear-gradient(90deg,var(--red) 0 50%,var(--blue) 50% 100%)}
.watermark{position:absolute;right:-70px;top:-50px;width:360px;height:360px;fill:none;stroke:var(--line);stroke-width:1.1;pointer-events:none}
.poster-copy,.poster-side{position:relative}.poster-copy{max-width:780px}
.bout{font-size:clamp(40px,7.4vw,78px);line-height:.92;margin:14px 0;display:flex;flex-wrap:wrap;align-items:center;gap:4px 18px}
.bout i{font:italic 800 .4em/1 var(--display);color:#fff;background:var(--red);padding:.2em .42em .14em;border-radius:3px}
.meta{margin:0;color:var(--muted);font-size:14px}
.status{display:inline-flex;align-items:center;gap:8px;font:700 15px/1 var(--display);letter-spacing:.9px;text-transform:uppercase;
padding:10px 14px 9px;border-radius:999px;border:1.5px solid var(--line-2);color:var(--ink-2)}
.status .ico{width:16px;height:16px;color:inherit}.status.live{border-color:var(--red);color:var(--red-ink)}
.pulse{width:9px;height:9px;border-radius:50%;background:var(--red);animation:pulse 1.8s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(200,16,46,.55)}70%,100%{box-shadow:0 0 0 9px rgba(200,16,46,0)}}
@media (prefers-reduced-motion:reduce){.pulse{animation:none}}

.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));background:var(--card);border:1px solid var(--line);
border-top:3px solid var(--ink);border-radius:0 0 4px 4px}
.cell{padding:15px 18px 17px;border-left:1px solid var(--line);min-width:0}.cell:first-child{border-left:0}
.cell .lbl{display:flex;align-items:center;gap:7px;font:700 13.5px/1 var(--display);letter-spacing:1.4px;text-transform:uppercase;color:var(--muted)}
.cell .lbl .ico{width:17px;height:17px}
.cell .val{display:block;font:800 32px/1.05 var(--display);margin:9px 0 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell .sub{display:block;font-size:13.5px;color:var(--muted)}

.slips{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:16px}
.slip{position:relative;background:var(--card);border:1px solid var(--line-2);border-radius:6px;padding:14px 18px 15px 22px}
.slip::before{content:"";position:absolute;left:0;top:0;bottom:0;width:6px;background:var(--red);border-radius:6px 0 0 6px}
.slip-top{display:flex;justify-content:space-between;align-items:center;min-height:30px}
.stamp{display:inline-flex;align-items:center;gap:6px;font:800 15px/1 var(--display);letter-spacing:2.2px;text-transform:uppercase;color:var(--red-ink)}
.stamp .ico{width:16px;height:16px}
.slip-id{font:700 12px/1 var(--display);letter-spacing:2.4px;text-transform:uppercase;color:var(--muted)}
.slip-main{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;margin-top:8px}
.slip-main h3{font-size:27px}.slip-main small{color:var(--muted);font-size:14px}
.odds{text-align:right}.odds span{display:block;font:700 12px/1 var(--display);letter-spacing:1.6px;text-transform:uppercase;color:var(--muted)}
.odds strong{font:800 46px/1 var(--display)}
.perf{position:relative;border-top:2px dashed var(--line-2);margin:14px -18px 12px -22px}
.perf::before,.perf::after{content:"";position:absolute;top:-10px;width:18px;height:18px;border-radius:50%;background:var(--paper);border:1px solid var(--line-2)}
.perf::before{left:-10px;clip-path:inset(0 0 0 50%)}.perf::after{right:-10px;clip-path:inset(0 50% 0 0)}
.slip-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0}.slip-stats div{min-width:0}
.slip-stats dt{font:700 12px/1 var(--display);letter-spacing:1.3px;text-transform:uppercase;color:var(--muted)}
.slip-stats dd{margin:5px 0 0;font:800 23px/1 var(--display)}
.empty{background:var(--card);border:1.5px dashed var(--line-2);border-radius:6px;padding:22px;color:var(--muted);text-align:center}

.legend{display:inline-flex;gap:6px;margin-left:auto;flex-wrap:wrap}
.tier{display:inline-block;font:700 13px/1 var(--display);letter-spacing:.8px;text-transform:uppercase;border-radius:3px;padding:4px 7px 3px;color:#fff}
.tier.strong{background:var(--strong)}.tier.lean{background:var(--lean)}.tier.toss{background:var(--toss)}
.res{display:inline-grid;place-items:center;min-width:30px;height:30px;padding:0 5px;border-radius:999px;font:800 15px/1 var(--display);
border:2px solid currentColor}
.res.win{color:var(--good)}.res.loss{color:var(--bad)}.res.void{color:var(--muted);font-size:12px}
.res.pending{color:var(--line-2);border-style:dashed}
.tag{display:inline-block;font:700 12px/1 var(--display);letter-spacing:.8px;text-transform:uppercase;border:1.5px solid var(--lean);
color:var(--lean);border-radius:3px;padding:3px 6px 2px}
.tag.accent{border-color:var(--red);color:var(--red-ink)}

.tape,.list{background:var(--card);border:1px solid var(--line);border-top:3px solid var(--ink);border-radius:0 0 4px 4px}
.bout-row{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1.5fr) minmax(0,1.15fr) 130px;gap:18px;align-items:center;
padding:14px 18px;border-top:1px solid var(--line)}
.tape-head,.fhead,.ehead{border-top:0;background:var(--card-2);padding-top:10px;padding-bottom:10px;font:700 13px/1 var(--display);
letter-spacing:1.4px;text-transform:uppercase;color:var(--muted)}
.tape-head span:nth-child(3),.tape-head span:last-child{text-align:right}
.corner b{display:block;font:700 22px/1.1 var(--display);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.corner small{display:flex;flex-wrap:wrap;gap:6px;align-items:center;color:var(--muted);font-size:13.5px;margin-top:3px}
.corner.opp{text-align:right}.corner.opp b{color:var(--ink-2);font-weight:600}.corner.opp small{justify-content:flex-end}
.split{display:flex;height:28px;border-radius:3px;overflow:hidden;background:var(--opp);cursor:default}
.seg{display:flex;align-items:center;font:800 16px/1 var(--display);padding:0 10px;white-space:nowrap}
.seg.strong{background:var(--strong);color:#fff}.seg.lean{background:var(--lean);color:#fff}.seg.toss{background:var(--toss);color:#fff}
.seg.opp{flex:1;justify-content:flex-end;color:var(--muted)}
.call{display:flex;justify-content:flex-end;align-items:center;gap:10px}

.tablewrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-top:3px solid var(--ink);border-radius:0 0 4px 4px}
table{border-collapse:collapse;width:100%;font-size:15px}
th,td{padding:10px 14px;text-align:left;border-top:1px solid var(--line);white-space:nowrap}
thead th{border-top:0;background:var(--card-2);font:700 13px/1 var(--display);letter-spacing:1.3px;text-transform:uppercase;color:var(--muted)}
tbody tr:hover td{background:var(--card-2)}tr.hl td{background:var(--red-soft);font-weight:700}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,470px),1fr));gap:16px;margin-top:16px}
.mini{display:inline-block;width:80px;height:8px;background:var(--opp);border-radius:99px;vertical-align:middle;overflow:hidden;margin-right:6px}
.mini span{display:block;height:100%;background:var(--red);border-radius:99px}

.rounds{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.round{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--red);border-radius:0 0 4px 4px;padding:16px 18px 18px}
.round:nth-child(even){border-top-color:var(--blue)}.round:nth-child(even) .ico{color:var(--blue)}
.rnum{display:block;font:800 13px/1 var(--display);letter-spacing:2.6px;text-transform:uppercase;color:var(--muted);margin-bottom:12px}
.round .ico{width:30px;height:30px}.round h3{margin:10px 0 6px}.round p{margin:0;color:var(--ink-2);font-size:15px}
.rules{margin:0;padding-left:20px}.rules li{margin:8px 0}.rules li::marker{color:var(--red)}

.chart{margin:0;background:var(--card);border:1px solid var(--line);border-radius:4px;padding:16px}
.chart figcaption{font:700 14px/1.2 var(--display);letter-spacing:1.2px;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
.chart svg{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .grid.zero{stroke:var(--muted);stroke-opacity:.5}
.chart .axis{fill:var(--muted);font-size:11px}.chart .label{fill:var(--ink);font-size:11.5px;font-weight:700}
.chart .line{fill:none;stroke:var(--series);stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}
.chart .dot,.chart .cross-dot{fill:var(--series);stroke:var(--card);stroke-width:2}.chart .cross-dot{opacity:0}
.chart .barfill{fill:var(--series)}.chart .barfill:hover{opacity:.85}
.chart .hit{fill:transparent}.chart .cross{stroke:var(--muted);stroke-width:1;opacity:0}
.tip{position:fixed;z-index:20;pointer-events:none;background:var(--ink);color:var(--paper);font-size:13px;padding:7px 10px;border-radius:4px;
max-width:280px;box-shadow:0 6px 18px rgba(0,0,0,.18);opacity:0;transition:opacity .08s}

.result-line{display:flex;align-items:center;gap:8px;margin:10px 0 0;font-size:15px;color:var(--ink-2)}
.lastcard{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:6px 18px;background:var(--card);border:1px solid var(--line);
border-left:5px solid var(--blue);border-radius:4px;padding:12px 18px;margin-bottom:18px;text-decoration:none;color:var(--ink)}
.lastcard:hover{border-color:var(--line-2);color:var(--ink)}.lastcard b{font:700 19px/1.15 var(--display)}
.lastcard .lc-line{grid-column:1/3;color:var(--ink-2);font-size:14.5px}.lastcard .lc-line b{font:inherit;font-weight:700}
.lastcard .lc-go{grid-row:1/3;grid-column:3;display:inline-flex;align-items:center;gap:6px;font:700 15px/1 var(--display);
letter-spacing:.9px;text-transform:uppercase;color:var(--red-ink)}
.foot{border-top:1px solid var(--line);background:var(--card);margin-top:16px}
.foot-in{display:grid;gap:12px;padding:24px 20px 30px}
.foot-brand{display:flex;gap:12px;align-items:flex-start}.foot-brand .logo{width:32px;height:32px}
.foot-brand p{margin:0;max-width:720px;color:var(--ink-2);font-size:14.5px}
.foot-small{margin:0;color:var(--muted);font-size:13px}
.age{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:50%;border:2px solid var(--red);color:var(--red-ink);
font:800 12px/1 var(--display);margin-right:6px;vertical-align:middle}

.tabs{display:inline-flex;background:var(--card);border:1.5px solid var(--line-2);border-radius:999px;padding:4px;margin-bottom:14px}
.tab{appearance:none;border:0;background:transparent;color:var(--ink-2);font:700 16px/1 var(--display);letter-spacing:1px;
text-transform:uppercase;padding:9px 16px 8px;border-radius:999px;display:inline-flex;align-items:center;gap:7px;cursor:pointer}
.tab .ico{width:16px;height:16px;color:inherit}.tab[aria-selected="true"]{background:var(--ink);color:var(--paper)}
.finder-bar{display:flex;gap:10px;flex-wrap:wrap}
.search{position:relative;flex:1 1 300px;min-width:0}
.search .ico{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--muted);pointer-events:none}
.finder-bar input,.finder-bar select{font:inherit;font-size:16px;color:var(--ink);background:var(--card);border:1.5px solid var(--line-2);
border-radius:6px;padding:11px 13px}
.search input{width:100%;padding-left:42px}.finder-bar select{font-weight:600;flex:0 1 auto}
.finder-bar input:focus,.finder-bar select:focus,.chip:focus-visible,.tab:focus-visible,.ev-link:focus-visible,.btn:focus-visible,
.theme:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:12px 0 4px}
.chips > span{font:700 13px/1 var(--display);letter-spacing:1.4px;text-transform:uppercase;color:var(--muted);margin-right:4px}
.chip{font:600 14px/1 var(--body);color:var(--ink-2);background:transparent;border:1.5px solid var(--line-2);border-radius:999px;
padding:6px 12px;cursor:pointer}.chip:hover{border-color:var(--red);color:var(--red-ink)}
.summary{margin:18px 0 12px;font-size:16px}
.frow,.erow{display:grid;gap:16px;align-items:center;padding:12px 18px;border-top:1px solid var(--line)}
.frow{grid-template-columns:minmax(0,1.8fr) minmax(0,1.3fr) minmax(0,1.1fr) 52px}
.erow{grid-template-columns:minmax(0,2fr) 96px minmax(0,1.2fr) 118px 132px}
.fhead,.ehead{border-top:0}.frow > span:last-child{text-align:right}
.who b{display:block;font:700 19px/1.15 var(--display);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.who b i{font-style:normal;font-weight:600;color:var(--muted);font-size:.8em;margin:0 3px}
.who small,.ev small{display:flex;gap:6px;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:13.5px;margin-top:2px}
.frow .tier{padding:3px 6px 2px}.frow .tag{text-transform:none;letter-spacing:.2px;white-space:nowrap}
.frow .tag b{display:inline;font:inherit;font-weight:800}
.ev-link{appearance:none;border:0;background:none;padding:0;color:var(--ink);font:700 19px/1.15 var(--display);text-align:left;cursor:pointer;
text-decoration:underline;text-decoration-color:var(--line-2);text-underline-offset:3px}
.ev-link:hover{color:var(--red-ink);text-decoration-color:currentColor}
.erow b{font:800 19px/1 var(--display)}.erow .score{font-size:23px}
.rate{display:flex;align-items:center;gap:10px}.rate-bar{flex:1;height:10px;background:var(--opp);border-radius:99px;overflow:hidden}
.rate-bar span{display:block;height:100%;border-radius:99px}.rate-bar .strong{background:var(--strong)}.rate-bar .lean{background:var(--lean)}
.rate-bar .toss{background:var(--toss)}
.more{margin:16px auto 0;display:flex}

@media (max-width:760px){
.wrap{padding:0 16px}main.wrap{padding:22px 16px 44px}
.top-in{flex-wrap:wrap;gap:0 12px;min-height:0;padding-top:10px}.word{font-size:26px}
.brand{order:1}.theme{order:2}nav{order:3;width:calc(100% + 32px);overflow-x:auto;scrollbar-width:none;margin:4px -16px 0;padding:0 8px}
nav::-webkit-scrollbar{display:none}nav a{padding:12px 10px 10px;font-size:16px;white-space:nowrap}
h1{font-size:38px}h2{font-size:22px}.legend{margin-left:0}
.poster{padding:24px 18px 20px}.watermark{width:230px;height:230px;right:-80px;top:-40px}
.board{grid-template-columns:1fr 1fr}.cell{border-left:0;border-top:1px solid var(--line);padding:12px 14px 14px}
.cell:nth-child(-n+2){border-top:0}.cell:nth-child(even){border-left:1px solid var(--line)}
.cell .val{font-size:25px;white-space:normal}
.slips{grid-template-columns:1fr}
.lastcard{grid-template-columns:1fr}.lastcard .lc-line{grid-column:1}.lastcard .lc-go{grid-row:auto;grid-column:1}
.tape-head,.fhead,.ehead{display:none}
.bout-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-areas:"pick opp" "split split" "call call";row-gap:10px;padding:14px 16px}
.corner.pick{grid-area:pick}.corner.opp{grid-area:opp}.split{grid-area:split}.call{grid-area:call;justify-content:space-between}
.corner b{font-size:19px}
.frow{grid-template-columns:minmax(0,1fr) minmax(0,1fr) 34px;row-gap:8px;column-gap:12px;align-items:start;padding:12px 16px}
.frow > span:first-child{grid-column:1/-1}.frow > span:last-child{grid-column:3;grid-row:2}
.erow{grid-template-columns:1fr 1fr;row-gap:10px;padding:12px 16px}.erow > .ev{grid-column:1/-1}
[data-l]::before{content:attr(data-l);display:block;font:700 11.5px/1 var(--display);letter-spacing:1.2px;text-transform:uppercase;
color:var(--muted);margin-bottom:5px}
}
"""

JS = r"""
(function(){
  var tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
  function show(e,t){tip.textContent=t;tip.style.opacity=1;move(e)}
  function move(e){var x=e.clientX+14,y=e.clientY+14;var r=tip.getBoundingClientRect();
    if(x+r.width>innerWidth-8)x=e.clientX-r.width-14;if(y+r.height>innerHeight-8)y=e.clientY-r.height-14;
    tip.style.left=x+'px';tip.style.top=y+'px'}
  function hide(){tip.style.opacity=0}
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mouseenter',function(e){show(e,el.getAttribute('data-tip'));
      var svg=el.ownerSVGElement;if(svg&&el.dataset.x){var c=svg.querySelector('.cross'),d=svg.querySelector('.cross-dot');
        c.setAttribute('x1',el.dataset.x);c.setAttribute('x2',el.dataset.x);c.style.opacity=1;d.setAttribute('cx',el.dataset.x);d.setAttribute('cy',el.dataset.y);d.style.opacity=1}});
    el.addEventListener('mousemove',move);
    el.addEventListener('mouseleave',function(){hide();var svg=el.ownerSVGElement;if(svg&&el.dataset.x){svg.querySelector('.cross').style.opacity=0;
      svg.querySelector('.cross-dot').style.opacity=0}});
  });
  document.querySelectorAll('time[datetime]').forEach(function(t){var d=new Date(t.getAttribute('datetime'));
    if(!isNaN(d))t.textContent=d.toLocaleString(undefined,{weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit'})});
  var theme=document.querySelector('.theme');
  if(theme)theme.addEventListener('click',function(){
    var root=document.documentElement,next=root.getAttribute('data-theme')==='dark'?'light':'dark';
    root.setAttribute('data-theme',next);try{localStorage.setItem('fightiq-theme',next)}catch(e){}
  });
})();
"""

SEARCH_JS = r"""
(function(){
  var root=document.getElementById('finder');if(!root)return;
  function $(id){return document.getElementById(id)}
  var q=$('q'),show=$('show'),sort=$('sort'),list=$('results'),summary=$('summary'),more=$('more'),
      headF=$('head-fights'),headE=$('head-events'),tabs=root.querySelectorAll('.tab');
  var PAGE=50,fights=[],events=[],items=[],shown=0,view='fights',timer;
  var day=new Intl.DateTimeFormat(undefined,{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
  function norm(s){return String(s).normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()}
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function pct(p){return Math.round(p*100)+'%'}
  function tier(p){return p>=0.7?'strong':p>=0.6?'lean':'toss'}
  function units(u){return '<b class="'+(u>0?'good':u<0?'bad':'')+'">'+(u>0?'+':'')+u.toFixed(2)+'u</b>'}
  function plural(n,w){return n.toLocaleString()+' '+w+(n===1?'':'s')}
  function when(d){return day.format(new Date(d+'T00:00:00Z'))}

  function load(d){
    events=d.events.map(function(e){return{date:e[0],name:e[1],fights:[],hay:' '+norm(e[1])+' '}});
    fights=d.fights.map(function(r){
      var e=events[r[0]],p=r[5]!=null?r[5]:r[4];
      var f={date:e.date,event:e.name,a:r[1],b:r[2],res:r[3],oa:r[6],ob:r[7],bet:r[8],debut:r[9],
             div:d.divisions[r[10]],method:d.methods[r[11]]};
      if(p!=null){f.side=p>=0.5?'a':'b';f.p=p>=0.5?p:1-p}
      f.winner=f.res===1?'a':f.res===0?'b':null;
      f.right=f.side&&f.winner?f.side===f.winner:null;
      if(f.side&&f.oa&&f.ob){f.odds=f.side==='a'?f.oa:f.ob;f.dog=f.odds>(f.side==='a'?f.ob:f.oa)}
      if(f.bet){f.betOdds=f.bet==='a'?f.oa:f.ob;f.pnl=f.bet===f.winner?f.betOdds-1:-1}
      f.hay=' '+norm(f.a)+' / '+norm(f.b)+' / '+norm(f.event)+' ';
      e.fights.push(f);
      return f;
    }).reverse();
    events.forEach(function(e){
      var picked=0,right=0,conf=0,n=0,bets=0,won=0;
      e.fights.forEach(function(f){
        if(f.p!=null){conf+=f.p;n++}
        if(f.right!=null){picked++;if(f.right)right++}
        if(f.bet){bets++;won+=f.pnl}
      });
      e.picked=picked;e.right=right;e.rate=picked?right/picked:null;e.conf=n?conf/n:null;e.bets=bets;e.won=won;
    });
    events.reverse();
    var params=new URLSearchParams(location.search);
    if(params.get('q'))q.value=params.get('q');
    pick(show,params.get('show'));pick(sort,params.get('sort'));
    setView(params.get('view')==='events'?'events':'fights',false);
  }
  function pick(sel,v){if(v&&sel.querySelector('option[value="'+v+'"]'))sel.value=v}

  function matcher(){
    var words=norm(q.value).split(' ').filter(Boolean),phrase=' '+words.join(' ');
    if(!words.length)return function(){return true};
    // a full name ("Jon Jones") matches as a phrase; otherwise every word can be anywhere ("McGregor Diaz")
    var exact=words.length>1&&fights.some(function(f){return f.hay.indexOf(phrase)>=0});
    return function(hay){
      if(exact)return hay.indexOf(phrase)>=0;
      for(var i=0;i<words.length;i++)if(hay.indexOf(' '+words[i])<0)return false;
      return true;
    };
  }

  function setView(v,remember){
    view=v;
    tabs.forEach(function(t){t.setAttribute('aria-selected',String(t.getAttribute('data-view')===v))});
    headF.hidden=v!=='fights';headE.hidden=v!=='events';show.hidden=v!=='fights';sort.hidden=v!=='events';
    q.placeholder=v==='events'?'Search an event or a fighter on the card':'Search a fighter or event, e.g. Jon Jones';
    run(remember);
  }

  function run(remember){
    var m=matcher(),mode=show.value;
    if(view==='fights'){
      items=fights.filter(function(f){
        if(!m(f.hay))return false;
        if(mode==='bets')return !!f.bet;
        if(mode==='upsets')return f.dog&&f.right;
        if(mode==='misses')return f.right===false;
        return true;
      });
      if(mode==='upsets')items.sort(function(x,y){return y.odds-x.odds});
    }else{
      items=events.filter(function(e){return m(e.hay)||e.fights.some(function(f){return m(f.hay)})});
      var s=sort.value,rated=function(e){return e.rate==null?(s==='best'?-1:2):e.rate};
      if(s==='best')items.sort(function(x,y){return rated(y)-rated(x)||y.picked-x.picked});
      if(s==='worst')items.sort(function(x,y){return rated(x)-rated(y)||y.picked-x.picked});
    }
    shown=0;list.innerHTML='';page();summarise();
    if(remember!==false){
      var url=new URL(location.href),p=url.searchParams;
      q.value.trim()?p.set('q',q.value.trim()):p.delete('q');
      view==='events'?p.set('view','events'):p.delete('view');
      view==='fights'&&mode!=='all'?p.set('show',mode):p.delete('show');
      view==='events'&&sort.value!=='new'?p.set('sort',sort.value):p.delete('sort');
      history.replaceState(null,'',url);
    }
  }

  function summarise(){
    var n=items.length;
    if(!n){summary.innerHTML='Nothing found. Try just a surname, like <b>Holloway</b>.';return}
    if(view==='events'){
      var rated=items.filter(function(e){return e.rate!=null}),avg=0,perfect=0;
      rated.forEach(function(e){avg+=e.rate;if(e.picked>=5&&e.right===e.picked)perfect++});
      summary.innerHTML='<b>'+plural(n,'event')+'</b>'+(rated.length?' · FightIQ picked <b>'+pct(avg/rated.length)
        +'</b> of winners per card on average · '+plural(perfect,'perfect card')+' (5+ fights, every pick right)':'');
      return;
    }
    var picked=0,right=0,bets=0,won=0,mode=show.value;
    items.forEach(function(f){if(f.right!=null){picked++;if(f.right)right++}if(f.bet){bets++;won+=f.pnl}});
    var s='<b>'+plural(n,'fight')+'</b>';
    if(mode==='upsets')s='<b>'+plural(n,'upset')+'</b> FightIQ called: it backed the betting underdog and the underdog won. Biggest odds first';
    else if(mode==='misses')s+=' FightIQ got wrong';
    else if(picked)s+=' · FightIQ picked the winner in <b>'+right.toLocaleString()+' of '+picked.toLocaleString()+'</b> ('+pct(right/picked)+')';
    if(bets)s+=' · '+plural(bets,'bet')+', '+units(won)+' at 1u each';
    summary.innerHTML=s;
  }

  function name(f,side){return side==='a'?f.a:f.b}

  function fightRow(f){
    var meta=when(f.date)+' · '+esc(f.event)+' · '+esc(f.div)+(f.debut?' <span class="tag">Debut</span>':'');
    var pickHtml='<b class="muted">No pick</b><small>nothing earlier to learn from</small>';
    if(f.side){
      pickHtml='<b>'+esc(name(f,f.side))+'</b><small><span class="tier '+tier(f.p)+'">'+pct(f.p)+'</span>'
        +(f.odds?' odds '+f.odds.toFixed(2):'')
        +(f.bet?' <span class="tag accent">Bet '+esc(name(f,f.bet).split(' ').pop())+' @ '+f.betOdds.toFixed(2)+' · '+units(f.pnl)+'</span>':'')
        +'</small>';
    }
    var won=f.winner?'<b>'+esc(name(f,f.winner))+'</b>':'<b>'+(f.res===0.5?'Draw':'No contest')+'</b>';
    var badge=f.right==null?'<span class="res void" title="No pick, draw or no contest">–</span>':
      f.right?'<span class="res win" title="FightIQ was right">✓</span>':'<span class="res loss" title="FightIQ was wrong">✗</span>';
    return '<div class="frow"><span class="who"><b>'+esc(f.a)+' <i>vs</i> '+esc(f.b)+'</b><small>'+meta+'</small></span>'
      +'<span class="who" data-l="FightIQ pick">'+pickHtml+'</span>'
      +'<span class="who" data-l="Winner">'+won+'<small>'+esc(f.method)+'</small></span>'
      +'<span>'+badge+'</span></div>';
  }

  function eventRow(e){
    var r=e.rate,cls=r==null?'toss':r>=0.7?'strong':r>=0.6?'lean':'toss';
    return '<div class="erow"><span class="ev"><button type="button" class="ev-link" data-event="'+esc(e.name)+'">'+esc(e.name)
      +'</button><small>'+when(e.date)+' · '+plural(e.fights.length,'fight')+'</small></span>'
      +'<span data-l="Picks right"><b class="score">'+(e.picked?e.right+'/'+e.picked:'–')+'</b></span>'
      +'<span data-l="Hit rate"><span class="rate"><span class="rate-bar"><span class="'+cls+'" style="width:'+(r==null?0:Math.round(r*100))
      +'%"></span></span><b>'+(r==null?'–':pct(r))+'</b></span></span>'
      +'<span data-l="Avg confidence"><b>'+(e.conf==null?'–':pct(e.conf))+'</b></span>'
      +'<span data-l="Bets">'+(e.bets?plural(e.bets,'bet')+' · '+units(e.won):'<span class="muted">No bets</span>')+'</span></div>';
  }

  function page(){
    list.insertAdjacentHTML('beforeend',items.slice(shown,shown+PAGE).map(view==='events'?eventRow:fightRow).join(''));
    shown=Math.min(shown+PAGE,items.length);
    more.hidden=shown>=items.length;
    if(!more.hidden)more.textContent='Show more ('+(items.length-shown).toLocaleString()+' left)';
  }

  q.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(run,150)});
  show.addEventListener('change',function(){run()});
  sort.addEventListener('change',function(){run()});
  more.addEventListener('click',page);
  tabs.forEach(function(t){t.addEventListener('click',function(){setView(t.getAttribute('data-view'))})});
  list.addEventListener('click',function(e){
    var b=e.target.closest('.ev-link');if(!b)return;
    q.value=b.getAttribute('data-event');show.value='all';setView('fights');root.scrollIntoView({behavior:'smooth'});
  });
  root.querySelectorAll('.chip').forEach(function(c){c.addEventListener('click',function(){q.value=c.textContent;run();q.focus()})});
  fetch(root.getAttribute('data-src')).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}).then(load)
    .catch(function(){summary.textContent="Couldn't load the fight data. Please refresh the page."});
})();
"""
