"""Get the bouts for an upcoming card: scrape ufcstats.com or read a CSV you fill in."""
from __future__ import annotations

import atexit
import os
import re
import sys
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
    """Fetch `url` until `ok(html)`: plain requests, browser-like requests, then a real browser.

    ufcstats.com can put a "Checking your browser... requires JavaScript" page in front of scripts;
    only a real browser gets past that, so it is the last resort.
    """
    html = ""
    for headers in HEADER_PROFILES:
        html = fetch(url, headers)
        if ok(html):
            return html, True
    if is_challenge(html):
        html = browser_get(url, ok)
        return html, ok(html)
    return html, False


CHALLENGE_MARKERS = ("checking your browser", "requires javascript", "just a moment")


def is_challenge(html: str) -> bool:
    text = html.lower()
    return len(html) < 20_000 and any(m in text for m in CHALLENGE_MARKERS)


PLAYWRIGHT_HELP = (
    "ufcstats.com is showing a 'Checking your browser' page to scripts, so FightIQ needs to use your browser.\n"
    "Install the browser helper once:   pip3 install playwright\n"
    "(it drives your Google Chrome; without Chrome also run:  python3 -m playwright install chromium)\n"
    "Or save the event page from Chrome (Cmd+S, 'Webpage, HTML Only') and use --event-html <file>."
)


class _Browser:
    """One browser session reused for every page, so the site's check only has to pass once."""

    def __init__(self):
        self._pw = self._browser = self._page = None
        self.headless = True

    def get(self, url: str, ok, wait_s: float = 25.0) -> str:
        html = self._load(url, ok, wait_s)
        if not ok(html) and self.headless and _can_show_window():
            # some checks don't clear in a hidden browser; retry with a visible window
            self.close()
            self.headless = False
            print("ufcstats.com is checking the browser - opening a Chrome window for a moment...")
            html = self._load(url, ok, wait_s)
        return html

    def _load(self, url, ok, wait_s) -> str:
        if self._page is None:
            self._start()
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # a navigation the check triggers mid-load can interrupt goto
            print(f"(browser: {str(exc).splitlines()[0]})")
        deadline = time.time() + wait_s
        html = ""
        while True:
            try:
                html = self._page.content()
            except Exception:  # the browser check is still redirecting ("page is navigating"): try again shortly
                pass  # keep the last page read, so a failure can still show what the site served
            if ok(html) or time.time() >= deadline:
                return html
            self._page.wait_for_timeout(1000)

    def _start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScrapeError(PLAYWRIGHT_HELP) from exc
        self._pw = sync_playwright().start()
        errors = []
        for options in ({"channel": "chrome"}, {}):  # your Google Chrome, else Playwright's Chromium
            try:
                self._browser = self._pw.chromium.launch(headless=self.headless, **options)
                break
            except Exception as exc:
                errors.append(str(exc).strip().splitlines()[0])
        else:
            self.close()
            raise ScrapeError("couldn't start a browser (" + "; ".join(errors) + ").\n" + PLAYWRIGHT_HELP)
        self._page = self._browser.new_page()
        atexit.register(self.close)

    def close(self) -> None:
        for obj, method in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        self._pw = self._browser = self._page = None


_browser = _Browser()


def _can_show_window() -> bool:
    """False on a server with no screen (GitHub's Linux runner), where a visible Chrome can't start."""
    return not sys.platform.startswith("linux") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def browser_get(url: str, ok) -> str:
    return _browser.get(url, ok)


def reset_browser() -> None:
    """Close the browser so the next page starts a fresh, hidden one (used between scrape attempts)."""
    _browser.close()
    _browser.headless = True


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
