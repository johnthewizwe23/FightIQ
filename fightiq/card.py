"""Get the bouts for an upcoming card: scrape ufcstats.com or read a CSV you fill in."""
from __future__ import annotations

import re
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import config

HEADERS = {"User-Agent": "Mozilla/5.0 (FightIQ; personal research)"}


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    time.sleep(0.5)  # be polite to ufcstats
    return resp.text


def fighter_url(href: str) -> str:
    """Normalise to the form used in the dataset: http://ufcstats.com/fighter-details/<id>."""
    m = re.search(r"fighter-details/([0-9a-f]+)", href or "")
    return f"http://ufcstats.com/fighter-details/{m.group(1)}" if m else ""


def parse_upcoming_events(html: str) -> list[dict]:
    """Events listed on ufcstats.com/statistics/events/upcoming, soonest first."""
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for a in soup.select('a[href*="event-details"]'):
        row = a.find_parent("tr")
        date_tag = row.select_one("span.b-statistics__date") if row else None
        date = _parse_date(date_tag.get_text(strip=True)) if date_tag else None
        events.append({"name": a.get_text(strip=True), "url": a["href"].strip(), "date": date})
    events.sort(key=lambda e: e["date"] or datetime.max)
    return events


def parse_event(html: str) -> dict:
    """Event name, date and bouts (fighter names, ufcstats URLs, weight class)."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h2.b-content__title")
    name = title.get_text(strip=True) if title else ""
    date = None
    for li in soup.select("li.b-list__box-list-item"):
        text = li.get_text(" ", strip=True)
        if text.lower().startswith("date:"):
            date = _parse_date(text.split(":", 1)[1])
    bouts = []
    for row in soup.select("tr.b-fight-details__table-row"):
        links = row.select('a[href*="fighter-details"]')
        if len(links) < 2:
            continue
        weight = ""
        for td in row.find_all("td"):
            t = td.get_text(" ", strip=True)
            if "weight" in t.lower():
                weight = t
                break
        bouts.append({
            "fighter_a": links[0].get_text(strip=True), "url_a": fighter_url(links[0]["href"]),
            "fighter_b": links[1].get_text(strip=True), "url_b": fighter_url(links[1]["href"]),
            "weight_class": weight,
        })
    return {"name": name, "date": date, "bouts": bouts}


def _parse_date(text: str):
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def scrape_upcoming(event_index: int = 0) -> tuple[pd.DataFrame, str, datetime | None]:
    """Bouts for the next (or Nth upcoming) UFC event on ufcstats.com."""
    events = parse_upcoming_events(fetch(config.UFCSTATS_UPCOMING))
    if not events:
        raise RuntimeError("no upcoming events found on ufcstats.com")
    ev = events[event_index]
    detail = parse_event(fetch(ev["url"]))
    card = pd.DataFrame(detail["bouts"])
    return card, detail["name"] or ev["name"], detail["date"] or ev["date"]


def list_upcoming() -> list[dict]:
    return parse_upcoming_events(fetch(config.UFCSTATS_UPCOMING))


def parse_odds(value) -> float:
    """Decimal odds from '1.65', '+150' or '-200'. Returns NaN if blank."""
    if value is None or (isinstance(value, float) and value != value):
        return float("nan")
    s = str(value).strip().replace("−", "-")
    if not s:
        return float("nan")
    if s[0] in "+-":
        a = float(s)
        return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)
    v = float(s)
    if v >= 100:  # American odds written without a plus sign
        return 1 + v / 100
    return v


_ALIASES = {
    "fighter_a": ["fighter_a", "fighter1", "fighter 1", "red", "red corner", "fighter"],
    "fighter_b": ["fighter_b", "fighter2", "fighter 2", "blue", "blue corner", "opponent"],
    "odds_a": ["odds_a", "odds1", "odds 1", "red odds", "odds_red"],
    "odds_b": ["odds_b", "odds2", "odds 2", "blue odds", "odds_blue"],
    "weight_class": ["weight_class", "weight class", "division"],
}


def read_card_csv(path) -> pd.DataFrame:
    """CSV with fighter_a, fighter_b and optionally odds_a, odds_b (decimal or American), weight_class."""
    raw = pd.read_csv(path, dtype=str).fillna("")
    cols = {c.strip().lower(): c for c in raw.columns}
    card = pd.DataFrame()
    for target, names in _ALIASES.items():
        src = next((cols[n] for n in names if n in cols), None)
        if src is not None:
            card[target] = raw[src].str.strip()
    if "fighter_a" not in card or "fighter_b" not in card:
        raise ValueError(f"{path}: need columns fighter_a and fighter_b")
    for c in ("odds_a", "odds_b"):
        card[c] = card[c].map(parse_odds) if c in card else float("nan")
    card = card[(card["fighter_a"] != "") & (card["fighter_b"] != "")]
    return card.reset_index(drop=True)
