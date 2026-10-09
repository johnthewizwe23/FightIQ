"""Scraper parsing, tested on small synthetic pages shaped like ufcstats.com markup."""
from datetime import datetime

from fightiq import card

UPCOMING = """
<table><tbody>
<tr class="b-statistics__table-row"><td class="b-statistics__table-col">
  <i class="b-statistics__table-content">
    <a href="http://ufcstats.com/event-details/bbb" class="b-link b-link_style_black">UFC 334: Later</a>
    <span class="b-statistics__date">November 14, 2026</span></i></td></tr>
<tr class="b-statistics__table-row"><td class="b-statistics__table-col">
  <i class="b-statistics__table-content">
    <a href="http://ufcstats.com/event-details/aaa" class="b-link b-link_style_black">UFC Fight Night: Sooner</a>
    <span class="b-statistics__date">October 17, 2026</span></i></td></tr>
</tbody></table>
"""

EVENT = """
<h2 class="b-content__title"><span class="b-content__title-highlight">UFC Fight Night: Sooner</span></h2>
<ul><li class="b-list__box-list-item"><i class="b-list__box-item-title">Date:</i> October 17, 2026</li></ul>
<table><tbody>
<tr class="b-fight-details__table-row b-fight-details__table-row__hover js-fight-details-click"
    data-link="http://ufcstats.com/fight-details/f1">
  <td class="b-fight-details__table-col"></td>
  <td class="b-fight-details__table-col l-page_align_left">
    <p><a href="http://ufcstats.com/fighter-details/111aaa" class="b-link b-link_style_black">Fighter One</a></p>
    <p><a href="http://ufcstats.com/fighter-details/222bbb" class="b-link b-link_style_black">Fighter Two</a></p></td>
  <td class="b-fight-details__table-col l-page_align_left"><p>Lightweight</p></td>
</tr>
</tbody></table>
"""


def test_parse_upcoming_events_sorted_soonest_first():
    events = card.parse_upcoming_events(UPCOMING)
    assert [e["name"] for e in events] == ["UFC Fight Night: Sooner", "UFC 334: Later"]
    assert events[0]["date"] == datetime(2026, 10, 17)


def test_parse_event():
    ev = card.parse_event(EVENT)
    assert ev["date"] == datetime(2026, 10, 17)
    assert ev["bouts"] == [{
        "fighter_a": "Fighter One", "url_a": "http://ufcstats.com/fighter-details/111aaa",
        "fighter_b": "Fighter Two", "url_b": "http://ufcstats.com/fighter-details/222bbb",
        "weight_class": "Lightweight",
    }]


def test_read_card_csv(tmp_path):
    p = tmp_path / "card.csv"
    p.write_text("Fighter 1,Fighter 2,Odds 1,Odds 2\nA Person,B Person,-150,+130\nC Person,D Person,,\n")
    c = card.read_card_csv(p)
    assert list(c["fighter_a"]) == ["A Person", "C Person"]
    assert round(c.at[0, "odds_a"], 4) == round(1 + 100 / 150, 4) and c.at[0, "odds_b"] == 2.3
    assert c["odds_a"].isna().iloc[1]


def _events_page(*events):
    """Event-list page with (name, date) rows, shaped like ufcstats markup."""
    rows = "".join(
        f'<tr class="b-statistics__table-row"><td class="b-statistics__table-col"><i>'
        f'<a href="http://ufcstats.com/event-details/{i:04x}" class="b-link b-link_style_black">{name}</a>'
        f'<span class="b-statistics__date">{d:%B %d, %Y}</span></i></td></tr>'
        for i, (name, d) in enumerate(events))
    return f"<table><tbody>{rows}</tbody></table>"


def test_list_upcoming_falls_back_to_completed_page(monkeypatch, tmp_path):
    from datetime import date, timedelta
    today = date.today()
    completed = _events_page(("Next Event", today + timedelta(days=7)), ("Last Week", today - timedelta(days=7)))
    pages = {card.EVENT_LIST_URLS[0]: "<html>nothing here</html>",
             card.EVENT_LIST_URLS[1]: "<html>nothing here</html>",
             card.EVENT_LIST_URLS[2]: completed}
    monkeypatch.setattr(card, "fetch", lambda url: pages[url])
    monkeypatch.setattr(card, "DEBUG_HTML", tmp_path / "debug.html")
    events = card.list_upcoming()
    assert [e["name"] for e in events] == ["Next Event"]


def test_list_upcoming_saves_page_when_nothing_found(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(card, "fetch", lambda url: "<html>blocked</html>")
    monkeypatch.setattr(card, "DEBUG_HTML", tmp_path / "debug.html")
    with pytest.raises(card.ScrapeError):
        card.list_upcoming()
    assert "blocked" in (tmp_path / "debug.html").read_text()
