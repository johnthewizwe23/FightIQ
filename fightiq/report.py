"""One-glance PDF of a card's predictions and bets (python3 -m fightiq report)."""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from . import config

INK = HexColor("#111318")
RED = HexColor("#D20A0A")
GREY = HexColor("#6B7280")
LIGHT = HexColor("#F3F4F6")
LINE = HexColor("#E5E7EB")
GREEN = HexColor("#15803D")
AMBER = HexColor("#D97706")
SLATE = HexColor("#9CA3AF")
HEADER = HexColor("#0E0F13")

PAGE_W, PAGE_H = A4
M = 14 * mm
CONTENT_W = PAGE_W - 2 * M
UNITS_PER_BANKROLL = 100

TIERS = [(0.70, "STRONG", GREEN), (0.60, "LEAN", AMBER), (0.0, "TOSS-UP", SLATE)]


def tier(p: float):
    for floor, label, color in TIERS:
        if p >= floor:
            return label, color
    return TIERS[-1][1:]


# ----------------------------------------------------------------------------- drawing helpers

def _octagon(c, cx, cy, r, fill, stroke=None, width=1.2):
    pts = [(cx + r * math.cos(math.radians(22.5 + 45 * k)), cy + r * math.sin(math.radians(22.5 + 45 * k)))
           for k in range(8)]
    path = c.beginPath()
    path.moveTo(*pts[0])
    for p in pts[1:]:
        path.lineTo(*p)
    path.close()
    c.setFillColor(fill)
    if stroke is not None:
        c.setStrokeColor(stroke)
        c.setLineWidth(width)
    c.drawPath(path, fill=1, stroke=1 if stroke is not None else 0)


def logo(c, cx, cy, r):
    _octagon(c, cx, cy, r, RED)
    _octagon(c, cx, cy, r * 0.78, HEADER)
    c.setFillColor(white)
    size = r * 0.95
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(cx, cy - size * 0.36, "IQ")


def target_icon(c, cx, cy, r, color=RED):
    c.setStrokeColor(color)
    c.setLineWidth(1)
    for k in (1.0, 0.62):
        c.circle(cx, cy, r * k, stroke=1, fill=0)
    c.setFillColor(color)
    c.circle(cx, cy, r * 0.25, stroke=0, fill=1)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        c.line(cx + dx * r * 0.8, cy + dy * r * 0.8, cx + dx * r * 1.25, cy + dy * r * 1.25)


def chart_icon(c, x, y, s, color=RED):
    c.setFillColor(color)
    for i, h in enumerate((0.45, 0.75, 1.0)):
        c.rect(x + i * s * 0.36, y, s * 0.26, s * h, stroke=0, fill=1)


def badge(c, x, y, w, h, text, fill, color=white, size=6.5, outline=False):
    if outline:
        c.setStrokeColor(fill)
        c.setLineWidth(0.8)
        c.setFillColor(white)
        c.roundRect(x, y, w, h, h / 2, stroke=1, fill=1)
        c.setFillColor(fill)
    else:
        c.setFillColor(fill)
        c.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
        c.setFillColor(color)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(x + w / 2, y + (h - size * 0.7) / 2, text)


def fit(c, text, font, size, max_w, min_size=6.5):
    while size > min_size and c.stringWidth(text, font, size) > max_w:
        size -= 0.25
    while c.stringWidth(text, font, size) > max_w and len(text) > 3:
        text = text[:-2] + "…" if not text.endswith("…") else text[:-2] + "…"
    return text, size


def text(c, x, y, s, font="Helvetica", size=9, color=INK, align="left", max_w=None):
    if max_w:
        s, size = fit(c, s, font, size, max_w)
    c.setFont(font, size)
    c.setFillColor(color)
    {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[align](x, y, s)


def footer(c, event, page):
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(M, 14 * mm, PAGE_W - M, 14 * mm)
    text(c, M, 9.5 * mm, f"UFC FightIQ  •  {event}", size=7, color=GREY, max_w=120 * mm)
    text(c, PAGE_W - M, 9.5 * mm, f"Bet responsibly  •  18+  •  {page}", size=7, color=GREY, align="right")


def header(c, event, when, title=None, tall=True):
    h = 40 * mm if tall else 26 * mm
    top = PAGE_H
    c.setFillColor(HEADER)
    c.rect(0, top - h, PAGE_W, h, stroke=0, fill=1)
    c.setFillColor(RED)
    c.rect(0, top - h - 1.6 * mm, PAGE_W, 1.6 * mm, stroke=0, fill=1)
    r = 10 * mm if tall else 7 * mm
    cy = top - h / 2
    logo(c, M + r, cy, r)
    x = M + 2 * r + 5 * mm
    if tall:
        text(c, x, cy + 7.5 * mm, "U F C", "Helvetica-Bold", 8, SLATE)
        c.setFont("Helvetica-Bold", 25)
        c.setFillColor(white)
        c.drawString(x, cy - 2.5 * mm, "FIGHT")
        c.setFillColor(RED)
        c.drawString(x + c.stringWidth("FIGHT", "Helvetica-Bold", 25), cy - 2.5 * mm, "IQ")
        text(c, x, cy - 9 * mm, title or "Weekly predictions", "Helvetica", 8.5, SLATE)
    else:
        c.setFont("Helvetica-Bold", 16)
        c.setFillColor(white)
        c.drawString(x, cy - 2 * mm, title or "")
    right = PAGE_W - M
    text(c, right, cy + (3 * mm if tall else 1 * mm), event, "Helvetica-Bold", 12 if tall else 10, white,
         align="right", max_w=95 * mm)
    if when is not None:
        text(c, right, cy - (3.5 * mm if tall else 4.5 * mm), f"{when:%A %d %B %Y}", "Helvetica", 8.5, SLATE,
             align="right")
    return top - h - 1.6 * mm


def kpi(c, x, y, w, h, label, value, sub="", value_color=INK):
    c.setFillColor(LIGHT)
    c.roundRect(x, y, w, h, 2 * mm, stroke=0, fill=1)
    text(c, x + 3.5 * mm, y + h - 5 * mm, label, "Helvetica-Bold", 6.5, GREY)
    text(c, x + 3.5 * mm, y + h - 12.5 * mm, value, "Helvetica-Bold", 15, value_color, max_w=w - 7 * mm)
    if sub:
        text(c, x + 3.5 * mm, y + 3.2 * mm, sub, "Helvetica", 7.5, GREY, max_w=w - 7 * mm)


# ----------------------------------------------------------------------------- data

def _rows(picks: pd.DataFrame) -> list[dict]:
    rows = []
    for _, p in picks.iterrows():
        a, b, pa = p["Fighter A"], p["Fighter B"], float(p["FightIQ P(A)"])
        pick_is_a = pa >= 0.5
        has_odds = not (pd.isna(p["Odds A"]) or pd.isna(p["Odds B"]))
        rows.append({
            "pick": a if pick_is_a else b, "opp": b if pick_is_a else a,
            "p": pa if pick_is_a else 1 - pa,
            "pick_odds": (p["Odds A"] if pick_is_a else p["Odds B"]) if has_odds else None,
            "opp_odds": (p["Odds B"] if pick_is_a else p["Odds A"]) if has_odds else None,
            "debut": "debut" in str(p.get("Notes", "")),
            "bet": p["Bet"] if isinstance(p["Bet"], str) and p["Bet"] else None,
            "bet_odds": p["Bet odds"], "ev": p["EV"], "stake": p["Stake (% bankroll)"],
            "bet_p": (pa if p["Bet"] == a else 1 - pa) if isinstance(p["Bet"], str) and p["Bet"] else None,
            "bet_opp": (b if p["Bet"] == a else a) if isinstance(p["Bet"], str) and p["Bet"] else None,
        })
    return rows


def record_from_tracking(rows: list[list]) -> dict | None:
    """Season record from Tracking rows (Date, Event, Fight, Pick, prob, Pick res, Bet on, odds, stake, Bet res)."""
    rows = [r for r in rows if "(example row)" not in str(r[1] or "")]
    picks = [r for r in rows if r[5] in ("W", "L")]
    bets = [r for r in rows if r[6] and r[9] in ("W", "L") and isinstance(r[7], (int, float))
            and isinstance(r[8], (int, float))]
    if not picks:
        return None
    wins = sum(r[5] == "W" for r in picks)
    staked = sum(r[8] for r in bets)
    profit = sum(r[8] * (r[7] - 1) if r[9] == "W" else -r[8] for r in bets)
    return {"wins": wins, "losses": len(picks) - wins, "bets": len(bets), "profit": profit,
            "roi": profit / staked if staked else None}


# ----------------------------------------------------------------------------- pages

def _predictions_page(c, rows, event, when):
    y = header(c, event, when)
    has_odds = any(r["pick_odds"] is not None for r in rows)
    bets = [r for r in rows if r["bet"]]

    # KPI tiles
    gap, h = 4 * mm, 21 * mm
    w = (CONTENT_W - 3 * gap) / 4
    y -= 6 * mm + h
    lock = max(rows, key=lambda r: r["p"])
    dogs = [r for r in rows if r["pick_odds"] is not None and r["pick_odds"] > r["opp_odds"]]
    upset = max(dogs, key=lambda r: r["p"]) if dogs else None
    kpi(c, M, y, w, h, "FIGHTS", str(len(rows)), "on the card")
    kpi(c, M + (w + gap), y, w, h, "VALUE BETS", str(len(bets)) if has_odds else "–",
        "see page 2" if has_odds else "add odds to unlock", RED if bets else INK)
    kpi(c, M + 2 * (w + gap), y, w, h, "LOCK OF THE WEEK", lock["pick"], f"{lock['p']:.0%} win chance")
    if upset:
        kpi(c, M + 3 * (w + gap), y, w, h, "UPSET ALERT", upset["pick"], f"underdog @ {upset['pick_odds']:.2f}  •  "
            f"{upset['p']:.0%}", RED)
    else:
        kpi(c, M + 3 * (w + gap), y, w, h, "UPSET ALERT", "–" if not has_odds else "None",
            "needs odds" if not has_odds else "model backs every favourite")

    # section title + legend
    y -= 11 * mm
    chart_icon(c, M, y - 0.5 * mm, 4.2 * mm)
    text(c, M + 6.5 * mm, y, "MODEL PREDICTIONS", "Helvetica-Bold", 11.5, INK)
    lx = PAGE_W - M
    for label, color in (("TOSS-UP  <60%", SLATE), ("LEAN  60-70%", AMBER), ("STRONG  70%+", GREEN)):
        bw = c.stringWidth(label, "Helvetica-Bold", 6) + 6 * mm
        lx -= bw
        badge(c, lx, y - 1 * mm, bw - 1.5 * mm, 4.4 * mm, label, color, size=6)

    # column heads
    y -= 7 * mm
    cols = {"pick": M + 4 * mm, "bar": M + 78 * mm, "pct": M + 136 * mm, "tier": M + 141 * mm, "odds": PAGE_W - M}
    for key, label, align in (("pick", "PICK", "left"), ("bar", "WIN CHANCE", "left"), ("tier", "CONFIDENCE", "left"),
                              ("odds", "ODDS", "right")):
        text(c, cols[key], y, label, "Helvetica-Bold", 6.5, GREY, align=align)
    y -= 2.5 * mm

    row_h = 12.5 * mm
    for i, r in enumerate(sorted(rows, key=lambda r: -r["p"])):
        if y - row_h < 18 * mm:
            footer(c, event, c.getPageNumber())
            c.showPage()
            y = header(c, event, when, "Model predictions (cont.)", tall=False) - 6 * mm
        y -= row_h
        label, color = tier(r["p"])
        if i % 2 == 0:
            c.setFillColor(LIGHT)
            c.rect(M, y, CONTENT_W, row_h, stroke=0, fill=1)
        c.setFillColor(color)
        c.rect(M, y, 1.4 * mm, row_h, stroke=0, fill=1)
        text(c, cols["pick"], y + 6.8 * mm, r["pick"], "Helvetica-Bold", 10.5, INK, max_w=66 * mm)
        text(c, cols["pick"], y + 2.6 * mm, f"vs {r['opp']}", "Helvetica", 7.5, GREY, max_w=50 * mm)
        if r["debut"]:
            badge(c, cols["pick"] + 52 * mm, y + 2.0 * mm, 12 * mm, 3.6 * mm, "DEBUT", AMBER, size=5.5, outline=True)
        bar_w, bar_h = 52 * mm, 3.2 * mm
        by = y + row_h / 2 - bar_h / 2
        c.setFillColor(LINE)
        c.roundRect(cols["bar"], by, bar_w, bar_h, bar_h / 2, stroke=0, fill=1)
        c.setFillColor(color)
        c.roundRect(cols["bar"], by, bar_w * r["p"], bar_h, bar_h / 2, stroke=0, fill=1)
        c.setStrokeColor(white)
        c.setLineWidth(0.8)
        c.line(cols["bar"] + bar_w / 2, by - 0.4 * mm, cols["bar"] + bar_w / 2, by + bar_h + 0.4 * mm)
        text(c, cols["pct"], y + row_h / 2 - 1.3 * mm, f"{r['p']:.0%}", "Helvetica-Bold", 10.5, INK, align="right")
        badge(c, cols["tier"], y + row_h / 2 - 2.3 * mm, 17 * mm, 4.6 * mm, label, color)
        odds = f"{r['pick_odds']:.2f}" if r["pick_odds"] is not None else "–"
        on_pick = r["bet"] == r["pick"]
        text(c, cols["odds"], y + row_h / 2 - 1.3 * mm, odds, "Helvetica-Bold" if on_pick else "Helvetica", 10,
             RED if on_pick else INK, align="right")
        if r["bet"] and not on_pick:  # value is on the other fighter
            text(c, cols["odds"], y + 1.6 * mm, f"value: {r['bet'].split()[-1]} {r['bet_odds']:.2f}",
                 "Helvetica-Bold", 6, RED, align="right")
        if r["bet"]:
            target_icon(c, cols["odds"] - 15 * mm, y + row_h / 2, 1.5 * mm)

    y -= 7 * mm
    note = ("Win chance = FightIQ model blended with bookmaker odds." if has_odds
            else "Win chance = FightIQ stats model. Add odds for the blended number and bets.")
    text(c, M, y, note + "  Sorted by confidence.", "Helvetica", 7, GREY)
    footer(c, event, c.getPageNumber())


def _bets_page(c, rows, event, when, record):
    y = header(c, event, when, "BETS OF THE WEEK", tall=False)
    has_odds = any(r["pick_odds"] is not None for r in rows)
    bets = sorted((r for r in rows if r["bet"]), key=lambda r: -(r["ev"] or 0))

    y -= 8 * mm
    target_icon(c, M + 2.5 * mm, y + 1.3 * mm, 2.4 * mm)
    text(c, M + 7.5 * mm, y, "VALUE BETS", "Helvetica-Bold", 11.5, INK)
    text(c, PAGE_W - M, y, "stake in units  •  1u = 1% of bankroll", "Helvetica", 7.5, GREY, align="right")
    y -= 5 * mm

    if not bets:
        h = 30 * mm
        y -= h
        c.setFillColor(LIGHT)
        c.roundRect(M, y, CONTENT_W, h, 3 * mm, stroke=0, fill=1)
        msg, sub = (("No value bets this week", "The model agrees with the bookies on every fight - no bet is the bet.")
                    if has_odds else ("Odds needed", "Run  python3 -m fightiq report  after the odds are in."))
        text(c, PAGE_W / 2, y + h / 2 + 1.5 * mm, msg, "Helvetica-Bold", 14, INK, align="center")
        text(c, PAGE_W / 2, y + h / 2 - 5 * mm, sub, "Helvetica", 8.5, GREY, align="center")
    else:
        gap, card_h = 4 * mm, 34 * mm
        card_w = (CONTENT_W - gap) / 2
        for i, r in enumerate(bets):
            col = i % 2
            if col == 0:
                y -= card_h + (gap if i else 0)
            if y < 20 * mm:
                footer(c, event, c.getPageNumber())
                c.showPage()
                y = header(c, event, when, "BETS OF THE WEEK (cont.)", tall=False) - 6 * mm - card_h
            x = M + col * (card_w + gap)
            c.setFillColor(white)
            c.setStrokeColor(LINE)
            c.setLineWidth(0.8)
            c.roundRect(x, y, card_w, card_h, 3 * mm, stroke=1, fill=1)
            c.setFillColor(RED)
            c.rect(x, y + 3 * mm, 1.6 * mm, card_h - 6 * mm, stroke=0, fill=1)
            target_icon(c, x + 8 * mm, y + card_h - 6.5 * mm, 2.3 * mm)
            text(c, x + 12.5 * mm, y + card_h - 7.6 * mm, "BACK", "Helvetica-Bold", 7, RED)
            text(c, x + 6 * mm, y + card_h - 15 * mm, r["bet"], "Helvetica-Bold", 13.5, INK, max_w=card_w - 32 * mm)
            text(c, x + 6 * mm, y + card_h - 19.5 * mm, f"vs {r['bet_opp']}", "Helvetica", 7.5, GREY,
                 max_w=card_w - 32 * mm)
            text(c, x + card_w - 5 * mm, y + card_h - 7.6 * mm, "ODDS", "Helvetica-Bold", 6.5, GREY, align="right")
            text(c, x + card_w - 5 * mm, y + card_h - 16.5 * mm, f"{r['bet_odds']:.2f}", "Helvetica-Bold", 21, INK,
                 align="right")
            stats = (("WIN CHANCE", f"{r['bet_p']:.0%}"), ("EDGE", f"+{r['ev']:.1%}"),
                     ("STAKE", f"{r['stake'] * UNITS_PER_BANKROLL:.1f}u"))
            sw = (card_w - 9 * mm) / 3
            for j, (lab, val) in enumerate(stats):
                sx = x + 6 * mm + j * sw
                c.setFillColor(LIGHT)
                c.roundRect(sx, y + 3 * mm, sw - 2 * mm, 9.5 * mm, 1.5 * mm, stroke=0, fill=1)
                text(c, sx + 2.5 * mm, y + 9 * mm, lab, "Helvetica-Bold", 5.8, GREY)
                text(c, sx + 2.5 * mm, y + 4.3 * mm, val, "Helvetica-Bold", 10.5, GREEN if lab == "EDGE" else INK)
        total = sum(r["stake"] for r in bets) * UNITS_PER_BANKROLL
        y -= 8 * mm
        text(c, M, y, f"{len(bets)} bet{'s' if len(bets) != 1 else ''}  •  {total:.1f}u total stake",
             "Helvetica-Bold", 9, INK)

    # how to read (+ record) needs about 50mm
    if y - (52 * mm if record else 24 * mm) < 18 * mm:
        footer(c, event, c.getPageNumber())
        c.showPage()
        y = header(c, event, when, "BETS OF THE WEEK (cont.)", tall=False) - 2 * mm
    y -= 14 * mm
    items = (("WIN CHANCE", "FightIQ's probability: stats model blended with the odds."),
             ("EDGE", "How far the odds beat that probability. Bets need 5%+."),
             ("STAKE", "Quarter Kelly, capped at 2u. No bets on UFC debuts."))
    w = (CONTENT_W - 8 * mm) / 3
    for j, (lab, desc) in enumerate(items):
        x = M + j * (w + 4 * mm)
        c.setFillColor(LIGHT)
        c.roundRect(x, y - 12 * mm, w, 16 * mm, 2 * mm, stroke=0, fill=1)
        text(c, x + 3.5 * mm, y, lab, "Helvetica-Bold", 7.5, RED)
        _wrap(c, x + 3.5 * mm, y - 4.5 * mm, desc, w - 7 * mm, 7.5)

    if record:
        y -= 34 * mm
        c.setFillColor(HEADER)
        c.roundRect(M, y, CONTENT_W, 15 * mm, 3 * mm, stroke=0, fill=1)
        text(c, M + 5 * mm, y + 9 * mm, "LIVE RECORD", "Helvetica-Bold", 7, SLATE)
        parts = [("PICKS", f"{record['wins']}-{record['losses']}"),
                 ("HIT RATE", f"{record['wins'] / max(1, record['wins'] + record['losses']):.0%}"),
                 ("BETS", str(record["bets"])),
                 ("PROFIT", f"{record['profit']:+.1f}u"),
                 ("ROI", f"{record['roi']:+.0%}" if record["roi"] is not None else "–")]
        pw = (CONTENT_W - 40 * mm) / len(parts)
        for j, (lab, val) in enumerate(parts):
            px = M + 38 * mm + j * pw
            text(c, px, y + 9 * mm, lab, "Helvetica-Bold", 6, SLATE)
            good = not val.startswith("-")
            color = white if lab not in ("PROFIT", "ROI") else (HexColor("#4ADE80") if good else HexColor("#F87171"))
            text(c, px, y + 3.5 * mm, val, "Helvetica-Bold", 12, color)
    footer(c, event, c.getPageNumber())


def _wrap(c, x, y, s, max_w, size, leading=None, color=GREY):
    leading = leading or size * 1.3
    words, line = s.split(), ""
    for w in words:
        trial = f"{line} {w}".strip()
        if c.stringWidth(trial, "Helvetica", size) > max_w and line:
            text(c, x, y, line, "Helvetica", size, color)
            y -= leading
            line = w
        else:
            line = trial
    if line:
        text(c, x, y, line, "Helvetica", size, color)


# ----------------------------------------------------------------------------- entry points

def render(picks: pd.DataFrame, event: str, when, out_path, record: dict | None = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    when = pd.Timestamp(when).to_pydatetime() if when is not None else None
    rows = _rows(picks)
    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle(f"UFC FightIQ - {event}")
    c.setAuthor("FightIQ")
    _predictions_page(c, rows, event, when)
    c.showPage()
    _bets_page(c, rows, event, when, record)
    c.showPage()
    c.save()
    return out_path


def report_path(event: str, when) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", event or "card").strip("_")[:60]
    day = pd.Timestamp(when).strftime("%Y-%m-%d") if when is not None else datetime.now().strftime("%Y-%m-%d")
    return config.OUTPUT_DIR / "reports" / f"FightIQ_{day}_{slug}.pdf"


def report_from_workbook(xlsx_path=None) -> Path:
    """Build the report from the card and odds currently in the workbook's Predictor."""
    from .predict import predict_card
    from .workbook import _read_predictor, _read_tracking
    xlsx_path = Path(xlsx_path or config.OUTPUT_DIR / "FightIQ_Model.xlsx")
    current = _read_predictor(xlsx_path)
    if current is None:
        raise SystemExit(f"no card found in {xlsx_path} - run: python3 -m fightiq workbook --upcoming")
    card, event, when = current
    n_odds = int((card["odds_a"].notna() & card["odds_b"].notna()).sum())
    print(f"card: {event or 'UFC card'} - odds for {n_odds} of {len(card)} bouts")
    if n_odds < len(card):
        print("  (odds are read from the saved file: after typing odds in Excel press Cmd+S, then run this again)")
    picks, _ = predict_card(card.reset_index(drop=True), event, when)
    return render(picks, event or "UFC card", when, report_path(event, when),
                  record_from_tracking(_read_tracking(xlsx_path)))
