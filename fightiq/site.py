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

    n = tr.settle(rows, fights)
    print(f"results filled in: {n}")
    write_tracking(tracking_csv, rows)
    try:
        print(f"fight finder: {history.write(state / HISTORY_FILE):,} past fights")
    except Exception as exc:  # never let the fight finder hold up the picks
        print(f"fight finder not updated: {exc}")
    return week


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


LOGO = ('<svg class="logo" viewBox="0 0 40 40" aria-hidden="true"><polygon points="{o}" fill="var(--red)"/>'
        '<polygon points="{i}" fill="#0e0f13"/><text x="20" y="25.5" text-anchor="middle" font-size="14" '
        'font-weight="800" fill="#fff" font-family="Inter,Helvetica,Arial,sans-serif">IQ</text></svg>')


def _octagon(r, c=20):
    import math
    return " ".join(f"{c + r * math.cos(math.radians(22.5 + 45 * k)):.2f},{c + r * math.sin(math.radians(22.5 + 45 * k)):.2f}"
                    for k in range(8))


LOGO = LOGO.format(o=_octagon(19), i=_octagon(15))

ICONS = {
    "data": '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>',
    "clock": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg>',
    "model": '<svg viewBox="0 0 24 24"><path d="M4 18 9 12l4 3 7-8"/><path d="M15 7h5v5"/></svg>',
    "target": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor"/></svg>',
    "lock": '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
    "check": '<svg viewBox="0 0 24 24"><path d="m5 12 4 4 10-10"/></svg>',
}


def icon(name: str) -> str:
    return f'<span class="ico">{ICONS[name]}</span>'


def layout(title: str, body: str, active: str, depth: int = 0, updated: str | None = None,
           script: str | None = None) -> str:
    up = "../" * depth
    links = []
    for key, href, label in (("week", "index.html", "This week"), ("record", "record.html", "Record"),
                             ("search", "search.html", "Past fights"), ("model", "model.html", "How it works")):
        on = ' class="on"' if key == active else ""
        links.append(f'<a href="{up}{href}"{on}>{label}</a>')
    nav = "".join(links)
    extra = f'<script src="{up}assets/{script}"></script>' if script else ""
    stamp = (f'Updated <time datetime="{esc(updated)}">{esc(updated.replace("T", " ")[:16])} UTC</time> · '
             if updated else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · UFC FightIQ</title>
<meta name="description" content="UFC FightIQ: free, fully transparent UFC fight predictions, value bets and a public track record.">
<link rel="icon" href="{up}assets/logo.svg" type="image/svg+xml">
<link rel="stylesheet" href="{up}assets/style.css">
</head>
<body>
<header class="top">
  <div class="wrap top-in">
    <a class="brand" href="{up}index.html">{LOGO}<span><small>UFC</small>FIGHT<b>IQ</b></span></a>
    <nav>{nav}</nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="foot">
  <div class="wrap">
    <p>{stamp}Free and fully transparent: every pick is published before the fight, locked at the first bout, and kept in the
    <a href="{REPO_URL}">public code repository</a>.</p>
    <p>Not financial advice. Bet responsibly · 18+ · Gambling Help: <a href="https://www.gamblinghelponline.org.au">gamblinghelponline.org.au</a>, 1800 858 858.</p>
  </div>
</footer>
<script src="{up}assets/app.js"></script>{extra}
</body>
</html>
"""


def kpis(items: list[tuple]) -> str:
    cells = []
    for item in items:
        label, value, sub = item[:3]
        cls = f" {item[3]}" if len(item) > 3 and item[3] else ""
        cells.append(f'<div class="kpi"><span class="lbl">{esc(label)}</span><span class="val{cls}">{value}</span>'
                     f'<span class="sub">{sub}</span></div>')
    return f'<section class="kpis">{"".join(cells)}</section>'


# ============================================================================ week view

def results_by_fight(rows: list[list]) -> dict:
    out = {}
    for r in rows:
        k = tr.row_key(r)
        if k:
            out[k] = r
    return out


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
    status = (f'<span class="pill locked">{icon("lock")}Picks locked</span>' if locked else
              f'<span class="pill live">Live · locks at <time datetime="{esc(week["locks_at"])}">'
              f'{esc(week["locks_at"].replace("T", " ")[:16])} UTC</time></span>')
    head = f"""
<section class="hero">
  <div><h1>{esc(week["event"])}</h1>
  <p class="muted">{day:%A %d %B %Y} · card from {esc(week["source"])} · first published
  <time datetime="{esc(week["published_at"])}">{esc(week["published_at"][:10])}</time></p></div>
  <div class="hero-side">{status}<a class="btn" href="{up}reports/{esc(week["slug"])}.pdf">{icon("check")}PDF</a></div>
</section>"""
    tiles = kpis([
        ("Fights", str(len(bouts)), "on the card"),
        ("Value bets", str(len(bets)) if has_odds else "–", "below" if bets else ("none this week" if has_odds else "no odds yet"),
         "accent" if bets else ""),
        ("Lock of the week", esc(lock["pick"]), f'{pct(lock["p"])} win chance'),
        ("Upset alert", esc(upset["pick"]) if upset else ("None" if has_odds else "–"),
         f'underdog @ {upset["odds"]:.2f} · {pct(upset["p"])}' if upset else ("model backs every favourite" if has_odds else "needs odds"),
         "accent" if upset else ""),
    ])
    if bets:
        cards = "".join(_bet_card(b, result(b, "bet")) for b in bets)
        total = sum(b["stake"] or 0 for b in bets)
        bets_html = (f'<h2>{icon("target")}Value bets <span class="muted small">{len(bets)} bets · {total:.1f}u · '
                     f'1u = 1% of bankroll</span></h2><div class="bets">{cards}</div>')
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
  <h2>{icon("model")}Model predictions {legend}</h2>
  <div class="preds" role="table" aria-label="Predictions">
    <div class="prow phead" role="row"><span>Pick</span><span>Win chance</span><span>Confidence</span><span>Odds</span><span>Result</span></div>
    {rows_html}
  </div>
  <p class="muted small">{note} Sorted by confidence. Full numbers: <a href="{up}data/weeks/{esc(week["slug"])}.json">JSON</a>.</p>
</section>"""


def _badge(result: str | None) -> str:
    if not result:
        return '<span class="res pending">–</span>'
    cls = {"W": "win", "L": "loss"}.get(result, "void")
    return f'<span class="res {cls}">{esc(result)}</span>'


def _pred_row(b: dict, result: str | None) -> str:
    label, cls = tier(b["p"])
    extra = []
    if b["debut"]:
        extra.append('<span class="tag">Debut</span>')
    if b["bet"] and b["bet"] != b["pick"]:
        extra.append(f'<span class="tag accent">Value: {esc(b["bet"].split()[-1])} @ {b["bet_odds"]:.2f}</span>')
    odds = f'{b["odds"]:.2f}' if b["odds"] else "–"
    odds_cls = ' class="accent"' if b["bet"] == b["pick"] else ""
    tip = (f'{esc(b["pick"])}: {pct(b["p"], 1)}'
           + (f' · model {pct(b["p_model"], 1)} · bookies {pct(b["p_market"], 1)}' if b["p_market"] is not None else ""))
    return f"""<div class="prow" role="row">
  <span class="who"><b>{esc(b["pick"])}</b><small>vs {esc(b["opponent"])} {"".join(extra)}</small></span>
  <span class="meter" data-tip="{tip}"><span class="bar {cls}" style="width:{b["p"] * 100:.1f}%"></span><i></i><em>{pct(b["p"])}</em></span>
  <span><span class="tier {cls}">{label.title() if label != "TOSS-UP" else "Toss-up"}</span></span>
  <span{odds_cls}>{odds}</span>
  <span>{_badge(result)}</span>
</div>"""


def _bet_card(b: dict, result: str | None) -> str:
    opp = b["opponent"] if b["bet"] == b["pick"] else b["pick"]
    return f"""<article class="bet">
  <div class="bet-top"><span class="back">{icon("target")}Back</span>{_badge(result) if result else ""}</div>
  <div class="bet-main"><div><h3>{esc(b["bet"])}</h3><small>vs {esc(opp)}</small></div>
  <div class="odds"><span class="lbl">Odds</span><strong>{b["bet_odds"]:.2f}</strong></div></div>
  <div class="bet-stats"><span><i>Win chance</i>{pct(b["bet_p"])}</span><span><i>Edge</i><b class="good">+{b["edge"] * 100:.1f}%</b></span>
  <span><i>Stake</i>{b["stake"]:.1f}u</span></div>
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
        ("Picks", f'{s["wins"]}-{s["losses"]}' if s["wins"] + s["losses"] else "–", "winners picked"),
        ("Hit rate", pct(s["wins"] / (s["wins"] + s["losses"])) if s["wins"] + s["losses"] else "–", "of settled picks"),
        ("Value bets", str(s["bets"]), f'{s["bet_wins"]} won' if s["bets"] else "none settled yet"),
        ("Profit", f'{s["profit"]:+.1f}u' if s["bets"] else "–", f'{s["staked"]:.1f}u staked' if s["bets"] else "",
         "good" if s["profit"] > 0 else ("bad" if s["profit"] < 0 else "")),
        ("ROI", pct(s["roi"], 1) if s["roi"] is not None else "–", f"since {since:%d %b %Y}" if since else "",
         "good" if (s["roi"] or 0) > 0 else ("bad" if (s["roi"] or 0) < 0 else "")),
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
    return f"""
<section class="hero"><div><h1>Live record</h1>
<p class="muted">Every pick and value bet since launch, settled from the official results. Nothing is ever removed or edited:
picks lock when the first fight starts, and the full history is in the <a href="{REPO_URL}/commits">public commit log</a>.</p></div>
<div class="hero-side"><a class="btn" href="data/tracking.csv">{icon("data")}CSV</a></div></section>
{tiles}
<section><h2>{icon("model")}Profit over time</h2>{cumulative_chart(s["path"])}</section>
<section><h2>{icon("check")}By event</h2>
<div class="tablewrap"><table><thead><tr><th>Date</th><th>Event</th><th>Picks</th><th>Bets</th><th>Profit</th></tr></thead>
<tbody>{ev_rows or '<tr><td colspan="5" class="muted">No events yet.</td></tr>'}</tbody></table></div></section>
<section><h2>{icon("data")}Every pick</h2>
<div class="tablewrap"><table class="log"><thead><tr><th>Date</th><th>Fight</th><th>Pick</th><th>Win chance</th><th>Result</th>
<th>Bet</th><th>Odds</th><th>Stake</th><th>Profit</th></tr></thead><tbody>{log or empty}</tbody></table></div>
<p class="muted small">Units: 1u = 1% of bankroll. Stakes are a quarter of the Kelly amount, capped at 2u. D = draw, NC = no contest
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
        ("data", "1 · Real fight data", "Round-by-round stats for every UFC bout since UFC 1, from ufcstats.com, refreshed daily."),
        ("clock", "2 · Only what was known", "Each fighter's numbers are rebuilt as they stood the day before the fight. No hindsight."),
        ("model", "3 · Compare the fighters", "A logistic regression turns 12 differences between the two fighters into a win chance."),
        ("target", "4 · Blend with the odds", "The model adjusts the bookmakers' price. A bet needs a 5%+ edge; stakes are small and capped."),
    ]
    step_html = "".join(f'<div class="step">{icon(i)}<h3>{esc(t)}</h3><p>{esc(d)}</p></div>' for i, t, d in steps)
    return f"""
<section class="hero"><div><h1>How FightIQ works</h1>
<p class="muted">Everything is public: the method, the code, the backtest and the live results. Here is the short version.</p></div></section>
<section class="steps">{step_html}</section>

<section><h2>{icon("check")}The headline numbers</h2>
{kpis([("FightIQ picks", pct(blend["accuracy"], 1), f"winners, {y0}–{y1} backtest", "good"),
       ("Bookmaker favourites", pct(market["accuracy"], 1), "same fights"),
       ("Stats model alone", pct(stats["accuracy"], 1), "without the odds"),
       ("Backtest bets", f'{rule["roi"] * 100:+.1f}% ROI', f'{rule["bets"]} bets, {rule["profit"]:+.0f}u')])}
<p>On its own, a stats model doesn't beat the betting market: bookmakers know a lot. FightIQ's edge comes from combining the two.
It starts from the market's price and nudges it using the stats, which beat the market's own probabilities on
{blend["n"]:,} historical fights (log loss {blend["log_loss"]:.4f} vs {market["log_loss"]:.4f}).</p></section>

<section><h2>{icon("model")}What the model looks at</h2>
<p class="muted">All numbers are fighter A minus fighter B. "Typical effect" is how much a normal-sized difference moves the win chance.</p>
<div class="tablewrap"><table><thead><tr><th>Factor</th><th>Direction</th><th>Typical effect</th></tr></thead><tbody>{feat_rows}</tbody></table></div>
<p class="muted small">Model trained on {trained.get("trained_fights", 0):,} UFC bouts ({esc(trained.get("trained_from"))} to {esc(trained.get("trained_to"))}).
Both fighters are always considered from both corners, so the order they're listed in can't change a prediction.
Blend: logit(p) = {bt["blend"]["market_logit"]:.3f} × logit(bookmaker p) + {bt["blend"]["model_score"]:.3f} × model score.</p></section>

<section><h2>{icon("target")}Bet rules</h2>
<ul class="rules">
<li><b>Edge 5%+</b>: a bet is only suggested when the best available odds beat FightIQ's win chance by 5% or more.</li>
<li><b>Small, capped stakes</b>: a quarter of the Kelly amount, never more than 2u (1u = 1% of bankroll).</li>
<li><b>No debut bets</b>: there are no UFC stats for debutants, and the backtest showed no edge on those fights.</li>
<li><b>Locked at fight time</b>: picks are final when the first bout starts and are never edited.</li>
</ul></section>

<section><h2>{icon("check")}Backtest: {y0}–{y1}, trained only on earlier years</h2>
{year_bars(bt["per_year"])}
<div class="grid2">
<div class="tablewrap"><table><thead><tr><th>Predictions</th><th>Fights</th><th>Accuracy</th><th>Log loss</th><th>Brier</th></tr></thead><tbody>{q_rows}</tbody></table></div>
<div class="tablewrap"><table><thead><tr><th>Betting strategy</th><th>Bets</th><th>Profit</th><th>ROI</th></tr></thead><tbody>{s_rows}</tbody></table></div>
<div class="tablewrap"><table><thead><tr><th>FightIQ said</th><th>Fights</th><th>Average</th><th>Actually won</th></tr></thead><tbody>{cal_rows}</tbody></table></div>
<div class="tablewrap"><table><thead><tr><th>Odds of the bet</th><th>Bets</th><th>ROI</th></tr></thead><tbody>{band_rows}</tbody></table></div>
</div>
<p class="muted small">Lower log loss and Brier scores are better. Bet rule: 95% range for ROI {pct(rule["roi_95ci"][0], 1)} to {pct(rule["roi_95ci"][1], 1)}
(resampling whole cards). Full report: <a href="{REPO_URL}/blob/main/reports/evaluation.md">evaluation.md</a>.
Look up any fight in this backtest on <a href="search.html">Past fights</a>.</p></section>

<section><h2>{icon("lock")}Honest caveats</h2>
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
    """The fight finder: a search box over every past fight; assets/search.js fills in the results."""
    head = """<section class="hero"><div><h1>Past fights</h1>
<p class="muted">{intro}</p></div>{side}</section>"""
    if not hist:
        return head.format(intro="Search every past UFC fight and see who FightIQ would have picked.", side="") + (
            '<section><div class="empty">The fight finder appears after the next daily update.</div></section>')
    first = hist["events"][0][0][:4]
    to = date.fromisoformat(hist["fights_to"])
    intro = (f"Search {len(hist['fights']):,} UFC fights from {first} to {to:%B %Y} and see who FightIQ would have picked. "
             "Every pick uses only what was known before the fight: each year is predicted by a model trained only "
             "on earlier years. No hindsight.")
    side = f'<div class="hero-side"><a class="btn" href="data/{HISTORY_FILE}">{icon("data")}JSON</a></div>'
    chips = "".join(f'<button class="chip" type="button">{esc(x)}</button>' for x in EXAMPLES)
    return head.format(intro=intro, side=side) + f"""
<section id="finder" data-src="data/{HISTORY_FILE}">
  <div class="finder-bar">
    <label class="vh" for="q">Search a fighter or event</label>
    <input id="q" type="search" placeholder="Search a fighter or event, e.g. Jon Jones" autocomplete="off" spellcheck="false">
    <label class="vh" for="show">Show</label>
    <select id="show">
      <option value="all">All fights</option><option value="bets">FightIQ bets only</option>
      <option value="upsets">Upsets FightIQ called</option><option value="misses">Picks it got wrong</option>
    </select>
  </div>
  <div class="chips"><span class="muted small">Try</span>{chips}</div>
  <p id="summary" class="summary" aria-live="polite">Loading fights…</p>
  <div class="preds fights">
    <div class="frow phead"><span>Fight</span><span>FightIQ pick</span><span>Winner</span><span>Right?</span></div>
    <div id="results"></div>
  </div>
  <button id="more" class="btn more" type="button" hidden>Show more</button>
  <noscript><div class="empty">The fight finder needs JavaScript. All the data is in the JSON file above.</div></noscript>
</section>
<p class="muted small">Win chance = FightIQ's blend of the stats model and the bookmakers' odds where historical odds exist
(2015 on), otherwise the stats model alone. Bets follow the site's rule (edge over 5%, no debuts) at a flat 1 unit, exactly as in
the <a href="model.html">backtest</a>. Before 2006 there were few earlier UFC fights to learn from, so those picks are rough.</p>"""


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
    (site / "assets" / "style.css").write_text(CSS)
    (site / "assets" / "app.js").write_text(JS)
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

    today = date.today()
    upcoming = [w for w in weeks if date.fromisoformat(w["date"]) >= today - pd.Timedelta(days=1).to_pytimedelta()]
    current = upcoming[0] if upcoming else (weeks[-1] if weeks else None)
    if current:
        body = week_body(current, rows, depth=0)
    else:
        body = ('<section class="hero"><div><h1>Next card coming soon</h1><p class="muted">Picks for the next UFC card are '
                'published here as soon as the card and odds are out (usually Thursday). Every pick is locked when the '
                'first fight starts.</p></div></section>'
                + f'<section><h2>{icon("model")}How it works</h2><p><a href="model.html">See the method, the backtest and the '
                  f'caveats</a>, or the <a href="record.html">live record</a>.</p></section>')
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
:root{color-scheme:light;--bg:#f6f7f9;--surface:#fff;--surface-2:#f1f2f5;--line:#e3e5ea;--text:#111318;--muted:#5d6472;
--red:#d20a0a;--head:#0e0f13;--good:#137a3a;--bad:#c22b2b;--strong:#15803d;--lean:#c26a04;--toss:#8b93a1;--series:#2a78d6;
--grid:#e6e8ec;--shadow:0 1px 2px rgba(16,24,40,.06)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--bg:#0b0c10;--surface:#15171d;
--surface-2:#1c1f26;--line:#2a2e37;--text:#eef0f4;--muted:#a3aab8;--good:#3ccf75;--bad:#f06b6b;--strong:#22a55a;--lean:#e0a030;
--toss:#6b7280;--series:#3987e5;--grid:#262a33;--shadow:none}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0b0c10;--surface:#15171d;--surface-2:#1c1f26;--line:#2a2e37;--text:#eef0f4;
--muted:#a3aab8;--good:#3ccf75;--bad:#f06b6b;--strong:#22a55a;--lean:#e0a030;--toss:#6b7280;--series:#3987e5;--grid:#262a33;--shadow:none}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--series)}a:hover{text-decoration:none}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px}
.top{background:var(--head);border-bottom:4px solid var(--red);position:sticky;top:0;z-index:5}
.top-in{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:64px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;color:#fff;text-decoration:none;font-weight:800;font-size:22px;letter-spacing:.5px}
.brand small{display:block;font-size:9px;letter-spacing:3px;color:#9ca3af;line-height:1}.brand b{color:var(--red)}
.brand span{line-height:1.05}.logo{width:38px;height:38px}
nav{display:flex;gap:4px;flex-wrap:wrap}nav a{color:#c9ced8;text-decoration:none;padding:8px 12px;border-radius:8px;font-weight:600;font-size:14px}
nav a:hover{color:#fff;background:#1d2028}nav a.on{color:#fff;background:#262a33}
main{padding:20px 16px 40px}
section{margin:22px 0}
h1{font-size:28px;line-height:1.15;margin:0 0 6px;letter-spacing:-.3px}
h2{font-size:17px;margin:0 0 12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;text-transform:uppercase;letter-spacing:.4px}
h3{margin:0;font-size:16px}
.muted{color:var(--muted)}.small{font-size:12.5px;font-weight:500;text-transform:none;letter-spacing:0}
.ico{display:inline-flex;width:20px;height:20px;color:var(--red)}.ico svg{width:100%;height:100%;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-top:8px}
.hero p{margin:0;max-width:640px}.hero-side{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:700;padding:6px 10px;border-radius:99px}
.pill .ico{width:15px;height:15px;color:inherit}.pill.locked{background:var(--surface-2);color:var(--text)}
.pill.live{background:rgba(210,10,10,.1);color:var(--red)}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--surface);border:1px solid var(--line);color:var(--text);
padding:7px 12px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px}.btn .ico{width:16px;height:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow);min-width:0}
.kpi .lbl{display:block;font-size:11px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;color:var(--muted)}
.kpi .val{display:block;font-size:22px;font-weight:800;margin:4px 0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi .sub{display:block;font-size:12.5px;color:var(--muted)}
.val.accent,.accent{color:var(--red)}.good{color:var(--good)}.bad{color:var(--bad)}
.bets{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.bet{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--red);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}
.bet-top{display:flex;justify-content:space-between;align-items:center}
.back{display:inline-flex;align-items:center;gap:6px;color:var(--red);font-weight:800;font-size:12px;text-transform:uppercase;letter-spacing:.6px}
.back .ico{width:17px;height:17px}
.bet-main{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;margin:6px 0 10px}
.bet-main small{color:var(--muted)}.odds{text-align:right}.odds .lbl{display:block;font-size:10.5px;font-weight:800;color:var(--muted);text-transform:uppercase}
.odds strong{font-size:28px;line-height:1}
.bet-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.bet-stats span{background:var(--surface-2);border-radius:8px;padding:6px 8px;font-weight:800}
.bet-stats i{display:block;font-style:normal;font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;font-weight:800}
.empty{background:var(--surface);border:1px dashed var(--line);border-radius:12px;padding:18px;color:var(--muted);text-align:center}
.legend{display:inline-flex;gap:6px;margin-left:auto;flex-wrap:wrap}
.tier{display:inline-block;font-size:11px;font-weight:800;color:#fff;border-radius:99px;padding:3px 9px;text-transform:uppercase;letter-spacing:.4px}
.tier.strong{background:var(--strong)}.tier.lean{background:var(--lean)}.tier.toss{background:var(--toss)}
.preds{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:var(--shadow)}
.prow{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1.5fr) 92px 56px 46px;gap:12px;align-items:center;padding:10px 14px;border-top:1px solid var(--line)}
.prow:first-child{border-top:0}.phead{font-size:11px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;background:var(--surface-2)}
.prow > span:nth-child(4),.prow > span:nth-child(5){text-align:right}
.who b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.who small{color:var(--muted);display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.tag{font-size:10.5px;font-weight:800;border:1px solid var(--lean);color:var(--lean);border-radius:99px;padding:0 6px;text-transform:uppercase}
.tag.accent{border-color:var(--red);color:var(--red)}
.meter{position:relative;display:block;height:10px;background:var(--surface-2);border-radius:99px;margin-right:48px;cursor:default}
.meter .bar{position:absolute;left:0;top:0;bottom:0;border-radius:99px}.bar.strong{background:var(--strong)}.bar.lean{background:var(--lean)}.bar.toss{background:var(--toss)}
.meter i{position:absolute;left:50%;top:-2px;bottom:-2px;width:2px;background:var(--surface)}
.meter em{position:absolute;right:-48px;top:-5px;font-style:normal;font-weight:800;width:42px;text-align:right}
.res{display:inline-block;min-width:26px;text-align:center;font-weight:800;font-size:12px;border-radius:6px;padding:2px 6px}
.res.win{background:rgba(19,122,58,.14);color:var(--good)}.res.loss{background:rgba(194,43,43,.13);color:var(--bad)}
.res.void{background:var(--surface-2);color:var(--muted)}.res.pending{color:var(--muted)}
.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px 12px;text-align:left;border-top:1px solid var(--line);white-space:nowrap}
thead th{border-top:0;background:var(--surface-2);font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
tr.hl td{background:rgba(210,10,10,.06);font-weight:700}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:12px;margin-top:12px}
.mini{display:inline-block;width:70px;height:8px;background:var(--surface-2);border-radius:99px;vertical-align:middle;overflow:hidden}
.mini span{display:block;height:100%;background:var(--series);border-radius:99px}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.step{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px;box-shadow:var(--shadow)}
.step .ico{width:28px;height:28px}.step h3{margin:8px 0 4px}.step p{margin:0;color:var(--muted);font-size:14px}
.rules{margin:0;padding-left:20px}.rules li{margin:6px 0}
.chart{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px;box-shadow:var(--shadow)}
.chart figcaption{font-size:12.5px;color:var(--muted);font-weight:700;margin-bottom:6px}
.chart svg{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .grid.zero{stroke:var(--muted);stroke-opacity:.5}
.chart .axis{fill:var(--muted);font-size:11px}.chart .label{fill:var(--text);font-size:11.5px;font-weight:700}
.chart .line{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart .dot,.chart .cross-dot{fill:var(--series);stroke:var(--surface);stroke-width:2}.chart .cross-dot{opacity:0}
.chart .barfill{fill:var(--series)}.chart .barfill:hover{opacity:.85}
.chart .hit{fill:transparent}.chart .cross{stroke:var(--muted);stroke-width:1;opacity:0}
.tip{position:fixed;z-index:20;pointer-events:none;background:var(--head);color:#fff;font-size:12.5px;padding:6px 9px;border-radius:7px;
max-width:280px;box-shadow:0 4px 12px rgba(0,0,0,.25);opacity:0;transition:opacity .08s}
.foot{border-top:1px solid var(--line);padding:18px 0 28px;color:var(--muted);font-size:13px}.foot p{margin:4px 0}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.finder-bar{display:flex;gap:8px;flex-wrap:wrap}
.finder-bar input,.finder-bar select{font:inherit;font-size:16px;color:var(--text);background:var(--surface);border:1px solid var(--line);
border-radius:10px;padding:10px 12px;box-shadow:var(--shadow)}
.finder-bar input{flex:1 1 260px;min-width:0}.finder-bar select{flex:0 1 auto;font-weight:600}
.finder-bar input:focus,.finder-bar select:focus{outline:2px solid var(--series);outline-offset:1px}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:10px 0 4px}
.chip{font:inherit;font-size:13px;font-weight:700;color:var(--text);background:var(--surface);border:1px solid var(--line);
border-radius:99px;padding:4px 11px;cursor:pointer}.chip:hover{border-color:var(--series);color:var(--series)}
.summary{margin:14px 0 10px}
.frow{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(0,1.25fr) minmax(0,1.1fr) 52px;gap:12px;align-items:center;
padding:10px 14px;border-top:1px solid var(--line)}
.frow.phead{border-top:0}.frow > span:last-child{text-align:right}
.frow .who b i{font-style:normal;font-weight:500;color:var(--muted)}
.frow .who small{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.frow .tier{padding:1px 7px}.frow .tag{text-transform:none;white-space:nowrap;font-size:11.5px;padding:1px 7px}.frow .tag b{display:inline;font-weight:800}
.frow .res{font-size:14px;min-width:30px;padding:3px 6px}
.more{margin:12px auto 0;display:flex;cursor:pointer;font-size:14px}
@media (max-width:640px){h1{font-size:23px}.prow{grid-template-columns:minmax(0,1fr) 44px 34px;row-gap:6px;column-gap:8px}
.prow > span:nth-child(2){grid-column:1/-1;grid-row:2}.prow > span:nth-child(3){display:none}
.phead{display:none}.prow > span:nth-child(4){grid-column:2;grid-row:1}.prow > span:nth-child(5){grid-column:3;grid-row:1}
.bets{grid-template-columns:1fr}.legend{margin-left:0}.kpi .val{white-space:normal;font-size:19px;line-height:1.2}
.frow{grid-template-columns:minmax(0,1fr) minmax(0,1fr) 34px;row-gap:8px;column-gap:10px;align-items:start}.frow.phead{display:none}
.frow > span:first-child{grid-column:1/-1}.frow > span:last-child{grid-column:3;grid-row:2}
.frow [data-l]::before{content:attr(data-l);display:block;font-size:10.5px;font-weight:800;color:var(--muted);
text-transform:uppercase;letter-spacing:.4px}}
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
})();
"""

SEARCH_JS = r"""
(function(){
  var root=document.getElementById('finder');if(!root)return;
  var q=document.getElementById('q'),show=document.getElementById('show'),list=document.getElementById('results'),
      summary=document.getElementById('summary'),more=document.getElementById('more');
  var PAGE=50,fights=[],matches=[],shown=0,timer;
  var day=new Intl.DateTimeFormat(undefined,{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
  function norm(s){return String(s).normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()}
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function pct(p){return Math.round(p*100)+'%'}
  function tier(p){return p>=0.7?'strong':p>=0.6?'lean':'toss'}
  function units(u){return '<b class="'+(u>0?'good':u<0?'bad':'')+'">'+(u>0?'+':'')+u.toFixed(2)+'u</b>'}

  function load(d){
    fights=d.fights.map(function(r){
      var e=d.events[r[0]],p=r[5]!=null?r[5]:r[4];
      var f={date:e[0],event:e[1],a:r[1],b:r[2],res:r[3],oa:r[6],ob:r[7],bet:r[8],debut:r[9],
             div:d.divisions[r[10]],method:d.methods[r[11]]};
      if(p!=null){f.side=p>=0.5?'a':'b';f.p=p>=0.5?p:1-p}
      f.winner=f.res===1?'a':f.res===0?'b':null;
      f.right=f.side&&f.winner?f.side===f.winner:null;
      if(f.side&&f.oa&&f.ob){f.odds=f.side==='a'?f.oa:f.ob;f.dog=f.odds>(f.side==='a'?f.ob:f.oa)}
      if(f.bet){f.betOdds=f.bet==='a'?f.oa:f.ob;f.pnl=f.bet===f.winner?f.betOdds-1:-1}
      f.hay=' '+norm(f.a)+' / '+norm(f.b)+' / '+norm(f.event)+' ';
      return f;
    }).reverse();
    var params=new URLSearchParams(location.search);
    if(params.get('q'))q.value=params.get('q');
    if(params.get('show')&&show.querySelector('option[value="'+params.get('show')+'"]'))show.value=params.get('show');
    run(false);
  }

  function run(remember){
    var words=norm(q.value).split(' ').filter(Boolean),mode=show.value,phrase=' '+words.join(' ');
    // a full name ("Jon Jones") matches as a phrase; otherwise every word can be anywhere ("McGregor Diaz")
    var exact=words.length>1&&fights.some(function(f){return f.hay.indexOf(phrase)>=0});
    matches=fights.filter(function(f){
      if(exact){if(f.hay.indexOf(phrase)<0)return false}
      else for(var i=0;i<words.length;i++)if(f.hay.indexOf(' '+words[i])<0)return false;
      if(mode==='bets')return !!f.bet;
      if(mode==='upsets')return f.dog&&f.right;
      if(mode==='misses')return f.right===false;
      return true;
    });
    if(mode==='upsets')matches.sort(function(x,y){return y.odds-x.odds});
    shown=0;list.innerHTML='';page();summarise();
    if(remember!==false){
      var url=new URL(location.href);
      words.length?url.searchParams.set('q',q.value.trim()):url.searchParams.delete('q');
      mode!=='all'?url.searchParams.set('show',mode):url.searchParams.delete('show');
      history.replaceState(null,'',url);
    }
  }

  function summarise(){
    var n=matches.length,picked=0,right=0,bets=0,won=0;
    matches.forEach(function(f){if(f.right!=null){picked++;if(f.right)right++}if(f.bet){bets++;won+=f.pnl}});
    if(!n){summary.innerHTML='No fights found. Try just a surname, like <b>Holloway</b>.';return}
    var mode=show.value,s='<b>'+n.toLocaleString()+'</b> fight'+(n===1?'':'s');
    if(mode==='upsets')s='<b>'+n.toLocaleString()+'</b> upset'+(n===1?'':'s')+' FightIQ called: it backed the betting underdog and the underdog won. Biggest odds first';
    else if(mode==='misses')s+=' FightIQ got wrong';
    else if(picked)s+=' · FightIQ picked the winner in <b>'+right.toLocaleString()+' of '+picked.toLocaleString()+'</b> ('+pct(right/picked)+')';
    if(bets)s+=' · '+bets+' bet'+(bets===1?'':'s')+', '+units(won)+' at 1u each';
    summary.innerHTML=s;
  }

  function name(f,side){return side==='a'?f.a:f.b}

  function row(f){
    var meta=day.format(new Date(f.date+'T00:00:00Z'))+' · '+esc(f.event)+' · '+esc(f.div)+(f.debut?' <span class="tag">Debut</span>':'');
    var pick='<b class="muted">No pick</b><small>nothing earlier to learn from</small>';
    if(f.side){
      pick='<b>'+esc(name(f,f.side))+'</b><small><span class="tier '+tier(f.p)+'">'+pct(f.p)+'</span>'
          +(f.odds?' odds '+f.odds.toFixed(2):'')
          +(f.bet?' <span class="tag accent">Bet '+esc(name(f,f.bet).split(' ').pop())+' @ '+f.betOdds.toFixed(2)+' · '+units(f.pnl)+'</span>':'')
          +'</small>';
    }
    var won=f.winner?'<b>'+esc(name(f,f.winner))+'</b>':'<b>'+(f.res===0.5?'Draw':'No contest')+'</b>';
    var badge=f.right==null?'<span class="res void" title="No pick, draw or no contest">–</span>':
      f.right?'<span class="res win" title="FightIQ was right">✓</span>':'<span class="res loss" title="FightIQ was wrong">✗</span>';
    return '<div class="frow"><span class="who"><b>'+esc(f.a)+' <i>vs</i> '+esc(f.b)+'</b><small>'+meta+'</small></span>'
      +'<span class="who" data-l="FightIQ pick">'+pick+'</span>'
      +'<span class="who" data-l="Winner">'+won+'<small>'+esc(f.method)+'</small></span>'
      +'<span>'+badge+'</span></div>';
  }

  function page(){
    list.insertAdjacentHTML('beforeend',matches.slice(shown,shown+PAGE).map(row).join(''));
    shown=Math.min(shown+PAGE,matches.length);
    more.hidden=shown>=matches.length;
    if(!more.hidden)more.textContent='Show more ('+(matches.length-shown).toLocaleString()+' left)';
  }

  q.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(run,150)});
  show.addEventListener('change',function(){run()});
  more.addEventListener('click',page);
  root.querySelectorAll('.chip').forEach(function(c){c.addEventListener('click',function(){q.value=c.textContent;run();q.focus()})});
  fetch(root.getAttribute('data-src')).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}).then(load)
    .catch(function(){summary.textContent="Couldn't load the fight data. Please refresh the page."});
})();
"""
