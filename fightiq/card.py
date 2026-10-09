"""Get the bouts for an upcoming card: scrape ufcstats.com or read a CSV you fill in."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import config

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# Request styles tried in turn: plain `requests` defaults first (what the daily Greco1899 scrape of
# ufcstats uses), then browser-like headers.
HEADER_PROFILES = [None, BROWSER_HEADERS]

# Pages that list the next event. The completed-events page shows the next event in its top row,
# so it is the fallback if the upcoming page can't be read. ufcstats.com only serves http.
EVENT_LIST_URLS = [
    config.UFCSTATS_UPCOMING,
    "http://ufcstats.com/statistics/events/completed",
]
_session = requests.Session()
DEBUG_HTML = config.OUTPUT_DIR / "ufcstats_debug.html"


class ScrapeError(Exception):
    """ufcstats.com answered, but the card couldn't be read from the page."""


def fetch(url: str, headers: dict | None = None) -> str:
    resp = _session.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    time.sleep(0.5)  # be polite to ufcstats
    return resp.text


def _fetch_until(url: str, ok) -> tuple[str, bool]:
    """Fetch `url` with each request style until `ok(html)`; returns (html, ok)."""
    html = ""
    for headers in HEADER_PROFILES:
        html = fetch(url, headers)
        if ok(html):
            return html, True
    return html, False


def page_summary(html: str) -> str:
    """Title and the start of the visible text, to show what a page actually was."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else "(no title)"
    text = soup.get_text(" ", strip=True)
    return f"title '{title}', text: {text[:200]!r}"


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
    events = list_upcoming()
    if event_index >= len(events):
        raise ScrapeError(f"only {len(events)} upcoming event(s) listed on ufcstats.com")
    ev = events[event_index]
    html, ok = _fetch_until(ev["url"], lambda h: bool(parse_event(h)["bouts"]))
    detail = parse_event(html)
    if not ok:
        _save_debug(ev["url"], html)
        raise ScrapeError(f"found the event '{ev['name']}' but couldn't read its bouts ({page_summary(html)}). "
                          f"What the site returned is saved in {DEBUG_HTML}")
    card = pd.DataFrame(detail["bouts"])
    return card, detail["name"] or ev["name"], detail["date"] or ev["date"]


def card_from_saved_page(path) -> tuple[pd.DataFrame, str, datetime | None]:
    """Card from an event page saved in the browser (File > Save Page As, 'Webpage, HTML Only')."""
    from pathlib import Path
    detail = parse_event(Path(path).expanduser().read_text(errors="ignore"))
    if not detail["bouts"]:
        raise ScrapeError(f"no bouts found in {path}. Save the ufcstats page of the event itself "
                          "(the one listing the bouts), not the events list.")
    return pd.DataFrame(detail["bouts"]), detail["name"], detail["date"]


def list_upcoming() -> list[dict]:
    """Upcoming events, soonest first, trying each event-list page until one can be read."""
    cutoff = date.today() - timedelta(days=2)  # event dates are US dates; allow for time zones
    problems, last = [], None
    def upcoming(html):
        return [e for e in parse_upcoming_events(html) if e["date"] is None or e["date"].date() >= cutoff]

    for url in EVENT_LIST_URLS:
        try:
            html, ok = _fetch_until(url, lambda h: bool(upcoming(h)))
        except requests.RequestException as exc:
            problems.append(f"{url}: {exc}")
            continue
        if ok:
            return upcoming(html)
        last = (url, html)
        problems.append(f"{url}: got a {len(html):,}-character page with no events - {page_summary(html)}")
    if last is None:
        raise requests.ConnectionError("; ".join(problems))
    _save_debug(*last)
    raise ScrapeError("couldn't find the next event on ufcstats.com.\n  " + "\n  ".join(problems)
                      + f"\nWhat the site returned is saved in {DEBUG_HTML}")


def _save_debug(url: str, html: str) -> None:
    DEBUG_HTML.parent.mkdir(parents=True, exist_ok=True)
    DEBUG_HTML.write_text(f"<!-- fetched from {url} at {datetime.now().isoformat(timespec='seconds')} -->\n{html}")


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
