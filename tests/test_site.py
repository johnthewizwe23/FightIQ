import json
from datetime import date, timedelta

import pandas as pd

from fightiq import site, tracking as tr


def _week(day, locks_at, bouts):
    return {"event": "UFC Test Night", "date": day, "slug": f"{day}_UFC-Test-Night", "source": "odds feed",
            "published_at": "2026-10-08T08:00:00+00:00", "updated_at": "2026-10-09T08:00:00+00:00",
            "locks_at": locks_at, "bouts": bouts}


BOUTS = [
    {"pick": "Brendan Allen", "opponent": "Christian Leroy Duncan", "p": 0.62, "p_model": 0.55, "p_market": 0.6,
     "odds": 1.9, "opp_odds": 1.95, "debut": False, "bet": "Brendan Allen", "bet_odds": 1.9, "bet_p": 0.62,
     "edge": 0.178, "stake": 2.0},
    {"pick": "Matheus Camilo", "opponent": "Jai Herbert", "p": 0.76, "p_model": 0.76, "p_market": None,
     "odds": None, "opp_odds": None, "debut": True, "bet": None, "bet_odds": None, "bet_p": None, "edge": None,
     "stake": None},
]


def test_build_with_no_state_shows_coming_soon(tmp_path):
    out = tmp_path / "site"
    site.build(tmp_path / "state", out)
    index = (out / "index.html").read_text()
    assert "Next card coming soon" in index
    record = (out / "record.html").read_text()
    assert "The record starts with the first card" in record
    for page in ("index.html", "record.html", "model.html", "search.html"):
        text = (out / page).read_text()
        assert ">None<" not in text and "NaN" not in text
    search = (out / "search.html").read_text()
    assert "appears after the next daily update" in search and "search.js" not in search


def test_build_with_a_week_and_settled_results(tmp_path):
    state, out = tmp_path / "state", tmp_path / "site"
    upcoming = (date.today() + timedelta(days=2)).isoformat()
    past = (date.today() - timedelta(days=7)).isoformat()
    (state / "weeks").mkdir(parents=True)
    for d in (upcoming, past):
        (state / "weeks" / f"{d}_UFC-Test-Night.json").write_text(json.dumps(_week(d, f"{d}T23:00:00+00:00", BOUTS)))
    rows = [[pd.Timestamp(past).to_pydatetime(), "UFC Test Night", "Brendan Allen vs Christian Leroy Duncan",
             "Brendan Allen", 0.62, "W", "Brendan Allen", 1.9, 2.0, "W"],
            [pd.Timestamp(past).to_pydatetime(), "UFC Test Night", "Matheus Camilo vs Jai Herbert",
             "Matheus Camilo", 0.76, "L", None, None, None, None]]
    site.write_tracking(state / "tracking.csv", rows)
    assert site.read_tracking(state / "tracking.csv")[0][tr.STAKE] == 2.0
    site.build(state, out)
    index = (out / "index.html").read_text()
    assert "UFC Test Night" in index and "Brendan Allen" in index and "+17.8%" in index and "Debut" in index
    record = (out / "record.html").read_text()
    assert "1-1" in record and "+1.80u" in record
    assert (out / "weeks" / f"{past}_UFC-Test-Night.html").exists()
    # home page shows the previous card's result; its own page shows it too
    assert "Last card" in index and "went <b>1-1</b>" in index and "+1.80u" in index
    assert "went <b>1-1</b>" in (out / "weeks" / f"{past}_UFC-Test-Night.html").read_text()
    assert (out / "data" / "weeks" / f"{upcoming}_UFC-Test-Night.json").exists()


def test_never_publishes_a_card_that_has_started(tmp_path):
    now = pd.Timestamp("2026-10-11T00:00:00Z")
    card = pd.DataFrame({"fighter_a": ["A"], "fighter_b": ["B"]})
    result = site._publish(card, "UFC Late", pd.Timestamp("2026-10-10"), pd.Timestamp("2026-10-10T23:00:00Z"),
                           "odds feed", tmp_path / "state", tmp_path / "site", now, [], predict_card=None)
    assert result is None and not list((tmp_path / "state" / "weeks").glob("*.json"))


def test_build_with_fight_history(tmp_path):
    from fightiq import history
    from tests.test_history import synthetic_history

    state, out = tmp_path / "state", tmp_path / "site"
    state.mkdir()
    P = history.predictions(synthetic_history())
    (state / site.HISTORY_FILE).write_text(history.to_json(P), encoding="utf-8")
    site.build(state, out)
    search = (out / "search.html").read_text()
    assert f"{len(P):,} UFC fights and" in search and "2003 to December 2017" in search
    assert 'data-view="events"' in search and "style.css?v=" in search
    assert 'href="search.html" class="on"' in search and "assets/search.js" in search
    assert json.loads((out / "data" / site.HISTORY_FILE).read_text())["fights_to"] == "2017-12-15"
    assert (out / "assets" / "search.js").exists()
    assert 'href="search.html"' in (out / "index.html").read_text()


def test_sync_tracking_drops_rows_left_over_from_an_earlier_version_of_a_card():
    day = "2026-10-10"
    week = _week(day, f"{day}T21:00:00+00:00", [dict(BOUTS[0]), dict(BOUTS[1], pick="Loopy Godinez", opponent="Ketlen Souza")])
    when = pd.Timestamp(day).to_pydatetime()
    rows = [[when, "UFC Test Night", "Brendan Allen vs Christian Leroy Duncan", "Brendan Allen", 0.62, None, None, None, None, None],
            [when, "UFC Test Night", "Lupita Godinez vs Ketlen Souza", "Lupita Godinez", 0.69, None, None, None, None, None],
            [when, "UFC Test Night", "Loopy Godinez vs Ketlen Souza", "Loopy Godinez", 0.71, None, None, None, None, None],
            [pd.Timestamp("2026-10-17").to_pydatetime(), "UFC Next", "A vs B", "A", 0.6, None, None, None, None, None]]
    assert site.sync_tracking(rows, [week]) == 1
    assert [r[tr.PICK] for r in rows] == ["Brendan Allen", "Loopy Godinez", "A"]


def test_home_page_moves_to_the_next_card_once_it_is_published(tmp_path):
    state, out = tmp_path / "state", tmp_path / "site"
    (state / "weeks").mkdir(parents=True)
    finished = (date.today() - timedelta(days=2)).isoformat()  # finished over a day ago
    nxt = (date.today() + timedelta(days=5)).isoformat()
    (state / "weeks" / f"{finished}_UFC-Test-Night.json").write_text(json.dumps(_week(finished, f"{finished}T00:00:00+00:00", BOUTS)))
    site.build(state, out)
    assert "Picks locked" in (out / "index.html").read_text()  # no newer card yet: keep showing the finished one
    week = dict(_week(nxt, f"{nxt}T21:00:00+00:00", BOUTS), event="UFC Next Night", slug=f"{nxt}_UFC-Next-Night")
    (state / "weeks" / f"{nxt}_UFC-Next-Night.json").write_text(json.dumps(week))
    site.build(state, out)
    index = (out / "index.html").read_text()
    assert "UFC Next Night" in index and "Last card" in index and "Picks locked" not in index


def test_first_posted_odds_and_bet_suggestions_survive_republishing():
    legacy = {"updated_at": "2026-10-11T14:36:00+00:00", "bouts": [
        dict(BOUTS[0], pick="Brendan Allen", opponent="Christian Leroy Duncan", odds=1.90, opp_odds=1.95,
             bet="Brendan Allen", bet_odds=1.90),
        dict(BOUTS[1], pick="Lupita Godinez", opponent="Ketlen Souza", odds=1.44, opp_odds=3.15, bet=None, bet_odds=None)]}
    new = [dict(BOUTS[0], odds=1.80, opp_odds=2.05, bet="Brendan Allen", bet_odds=1.80),
           dict(BOUTS[1], pick="Loopy Godinez", opponent="Ketlen Souza", odds=1.40, opp_odds=3.30, bet="Ketlen Souza",
                bet_odds=3.30)]
    site.carry_odds_history(new, legacy, "2026-10-12T08:00:00+00:00")
    assert new[0]["first_odds"] == {"Brendan Allen": 1.90, "Christian Leroy Duncan": 1.95}
    assert new[0]["bet_first"] == {"side": "Brendan Allen", "odds": 1.90, "at": "2026-10-11T14:36:00+00:00"}
    assert new[1]["first_odds"] == {"Lupita Godinez": 1.44, "Ketlen Souza": 3.15}  # spelling changed, same fighter
    assert new[1]["bet_first"] == {"side": "Ketlen Souza", "odds": 3.30, "at": "2026-10-12T08:00:00+00:00"}
    again = [dict(b) for b in new]
    again[0]["bet_odds"] = 1.75
    site.carry_odds_history(again, {"updated_at": "2026-10-12T08:00:00+00:00", "bouts": new}, "2026-10-13T08:00:00+00:00")
    assert again[0]["bet_first"]["odds"] == 1.90 and again[1]["bet_first"]["odds"] == 3.30


def test_record_page_shows_the_closing_line_check(tmp_path):
    state, out = tmp_path / "state", tmp_path / "site"
    (state / "weeks").mkdir(parents=True)
    past = (date.today() - timedelta(days=7)).isoformat()
    # Allen: suggested at 2.00, closed 1.90 (still a bet). Duncan: suggested earlier at 1.80, closed 1.95, no longer a bet
    bouts = [dict(BOUTS[0], suggested={"Brendan Allen": {"odds": 2.00, "at": f"{past}T08:00:00+00:00"},
                                       "Christian Leroy Duncan": {"odds": 1.80, "at": f"{past}T02:00:00+00:00"}})]
    (state / "weeks" / f"{past}_UFC-Test-Night.json").write_text(json.dumps(_week(past, f"{past}T21:00:00+00:00", bouts)))
    site.build(state, out)
    record = (out / "record.html").read_text()
    assert "Beating the closing line?" in record and "1/2" in record and "+5.3%" in record and "-7.7%" in record
    assert "<td>Yes</td>" in record and "<td>No</td>" in record
    site.build(tmp_path / "empty", tmp_path / "site2")
    assert "Tracking starts with the next card" in (tmp_path / "site2" / "record.html").read_text()


def test_a_suggestion_survives_the_bet_disappearing_or_flipping_and_names_in_either_order():
    card = {"updated_at": "t0", "bouts": []}
    rounds = [("Steven Koslow", 2.10), (None, None), ("Chad Anheliger", 1.95), ("Steven Koslow", 2.25)]
    for i, (bet, bet_odds) in enumerate(rounds, start=1):
        bouts = [dict(BOUTS[0], pick="Steven Koslow", opponent="Chad Anheliger", odds=2.10, opp_odds=1.80,
                      bet=bet, bet_odds=bet_odds)]
        site.carry_odds_history(bouts, card, f"t{i}")
        card = {"updated_at": f"t{i}", "bouts": bouts}
    b = card["bouts"][0]
    assert b["suggested"] == {"Steven Koslow": {"odds": 2.10, "at": "t1"}, "Chad Anheliger": {"odds": 1.95, "at": "t3"}}
    assert b["bet_first"] == {"side": "Steven Koslow", "odds": 2.10, "at": "t1"}
    assert site._same_name("Zhang Weili", "Weili Zhang") and not site._same_name("Zhang Weili", "Yan Xiaonan")


def test_an_unlocked_card_keeps_its_official_bouts_and_its_lock_follows_them(tmp_path, monkeypatch):
    """Oct 2026: the odds-feed fallback added two other-promotion bouts and pulled the lock 7 hours early."""
    state, out = tmp_path / "state", tmp_path / "site"
    (state / "weeks").mkdir(parents=True)
    day = pd.Timestamp("2026-10-17")
    existing = dict(_week("2026-10-17", "2026-10-17T14:00:00+00:00", BOUTS), event="UFC Fight Night: Real vs. Card",
                    slug="2026-10-17_UFC-Fight-Night-Real-vs-Card",
                    official=[["Brendan Allen", "Christian Leroy Duncan"], ["Matheus Camilo", "Jai Herbert"]])
    (state / "weeks" / "2026-10-17_UFC-Fight-Night-Real-vs-Card.json").write_text(json.dumps(existing))
    assert json.loads(site._card_file(state, day).read_text())["official"] == existing["official"]
    seen = {}

    def fake_predict(card, name, when):
        seen["card"] = card
        picks = pd.DataFrame({"Fighter A": card["fighter_a"], "Fighter B": card["fighter_b"]})
        return picks, None
    monkeypatch.setattr(site, "_bouts", lambda picks: [dict(BOUTS[0])])
    monkeypatch.setattr(site.report_mod, "render", lambda *a, **k: None)
    monkeypatch.setattr(site.tr, "picks_to_rows", lambda picks, require_odds=False: [])
    card = pd.DataFrame({"fighter_a": ["Brendan Allen"], "fighter_b": ["Christian Leroy Duncan"]})
    week = site._publish(card, "UFC: Allen vs. Duncan", day, pd.Timestamp("2026-10-17T21:00:00Z"), "odds feed",
                         state, out, pd.Timestamp("2026-10-15T13:00:00Z"), [], fake_predict)
    assert week["locks_at"] == "2026-10-17T21:00:00+00:00"  # corrected later, allowed because it had not locked yet
    assert week["official"] == existing["official"] and week["event"] == "UFC Fight Night: Real vs. Card"
    # once the (corrected) lock time has passed, nothing changes any more
    again = site._publish(card, "x", day, pd.Timestamp("2026-10-18T01:00:00Z"), "odds feed", state, out,
                          pd.Timestamp("2026-10-17T21:30:00Z"), [], fake_predict)
    assert again["locks_at"] == "2026-10-17T21:00:00+00:00" and again["updated_at"] == week["updated_at"]


def test_the_lock_never_moves_later_close_to_the_published_lock(tmp_path, monkeypatch):
    """A race-day run whose feed is missing the first bout must not reopen the picks."""
    state, out = tmp_path / "state", tmp_path / "site"
    (state / "weeks").mkdir(parents=True)
    day = pd.Timestamp("2026-10-17")
    existing = dict(_week("2026-10-17", "2026-10-17T21:00:00+00:00", BOUTS), slug="2026-10-17_UFC-Test")
    (state / "weeks" / "2026-10-17_UFC-Test.json").write_text(json.dumps(existing))
    monkeypatch.setattr(site, "_bouts", lambda picks: [dict(BOUTS[0])])
    monkeypatch.setattr(site.report_mod, "render", lambda *a, **k: None)
    monkeypatch.setattr(site.tr, "picks_to_rows", lambda picks, require_odds=False: [])
    predict = lambda card, name, when: (pd.DataFrame({"Fighter A": card["fighter_a"]}), None)
    card = pd.DataFrame({"fighter_a": ["Brendan Allen"], "fighter_b": ["Christian Leroy Duncan"]})
    later = pd.Timestamp("2026-10-17T21:30:00Z")
    week = site._publish(card, "x", day, later, "odds feed", state, out, pd.Timestamp("2026-10-17T20:30:00Z"), [], predict)
    assert week["locks_at"] == "2026-10-17T21:00:00+00:00"
    again = site._publish(card, "x", day, later, "odds feed", state, out, pd.Timestamp("2026-10-17T21:20:00Z"), [], predict)
    assert again["updated_at"] == week["updated_at"]  # locked at 21:00 as published
    # an earlier first bout always moves it earlier
    early = site._publish(card, "x", day, pd.Timestamp("2026-10-17T20:45:00Z"), "odds feed", state, out,
                          pd.Timestamp("2026-10-17T20:31:00Z"), [], predict)
    assert early["locks_at"] == "2026-10-17T20:45:00+00:00"


def test_official_card_takes_late_replacements_from_the_feed_and_is_found_a_day_off(tmp_path):
    official = [["Steven Koslow", "Chad Anheliger"], ["Mike Malott", "Joaquin Buckley"], ["Ana One", "Bea Two"]]
    feed = [["Joaquin Buckley", "Mike Malott"], ["Steven Koslow", "Jose Newguy"], ["Other Show", "Some Guy"]]
    assert site._with_official(official, feed) == [["Steven Koslow", "Jose Newguy"], ["Mike Malott", "Joaquin Buckley"],
                                                    ["Ana One", "Bea Two"]]
    state = tmp_path / "state"
    (state / "weeks").mkdir(parents=True)
    (state / "weeks" / "2026-11-15_UFC-Sydney.json").write_text(json.dumps(dict(
        _week("2026-11-15", "2026-11-14T23:00:00+00:00", BOUTS), official=official)))
    found = site._card_file(state, pd.Timestamp("2026-11-14"), feed + [["Bea Two", "Ana One"]])  # feed's US date
    assert found is not None and found.name.startswith("2026-11-15")
    assert site._card_file(state, pd.Timestamp("2026-11-14"), [["Steven Koslow", "Chad Anheliger"]]) is None


def test_debut_tag_sits_with_the_debuting_fighter():
    b = dict(BOUTS[0], pick="SuYoung You", opponent="Cody Chovancek", debut=True, debut_names=["Cody Chovancek"],
             bet=None, bet_odds=None)
    row = site._pred_row(b, None)
    pick_cell, opp_cell = row.split('class="corner opp"')
    assert "UFC debut" not in pick_cell and "UFC debut" in opp_cell


def test_fight_table_opens_by_confidence_and_can_switch_to_card_order(tmp_path):
    state, out = tmp_path / "state", tmp_path / "site"
    (state / "weeks").mkdir(parents=True)
    upcoming = (date.today() + timedelta(days=2)).isoformat()
    week = dict(_week(upcoming, f"{upcoming}T23:00:00+00:00", BOUTS), source="ufcstats.com")
    (state / "weeks" / f"{upcoming}_UFC-Test-Night.json").write_text(json.dumps(week))
    site.build(state, out)
    index = (out / "index.html").read_text()
    # Camilo (76%) is listed first, but Allen vs Duncan is the main event (card position 1) for the toggle
    assert index.index('data-card="2" data-p="0.7600"') < index.index('data-card="1" data-p="0.6200"')
    assert 'data-sort="card" aria-pressed="false">Card order</button>' in index
    assert 'class="slot">Main event</span>' in index and 'class="slot">Co-main</span>' in index
    assert "fightiq-sort" in (out / "assets" / "app.js").read_text()
    # a card that only came from the odds feed has no reliable running order, so no "Main event" label
    (state / "weeks" / f"{upcoming}_UFC-Test-Night.json").write_text(json.dumps(dict(week, source="odds feed")))
    site.build(state, out)
    assert 'class="slot"' not in (out / "index.html").read_text()


def test_next_card_stays_hidden_until_the_current_card_has_finished(tmp_path):
    """Oct 2026: next week's card took over the home page an hour into this week's card."""
    from datetime import datetime, timezone
    state, out = tmp_path / "state", tmp_path / "site"
    (state / "weeks").mkdir(parents=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = now - timedelta(hours=1)  # this week's card locked an hour ago
    today, nxt = started.date().isoformat(), (started.date() + timedelta(days=7)).isoformat()
    (state / "weeks" / f"{today}_UFC-Test-Night.json").write_text(json.dumps(_week(today, started.isoformat(), BOUTS)))
    week = dict(_week(nxt, f"{nxt}T21:00:00+00:00", BOUTS), event="UFC Next Night", slug=f"{nxt}_UFC-Next-Night")
    (state / "weeks" / f"{nxt}_UFC-Next-Night.json").write_text(json.dumps(week))
    rows = [[pd.Timestamp(d).to_pydatetime(), e, "Brendan Allen vs Christian Leroy Duncan", "Brendan Allen", 0.62,
             None, None, None, None, None] for d, e in ((today, "UFC Test Night"), (nxt, "UFC Next Night"))]
    site.write_tracking(state / "tracking.csv", rows)
    (out / "weeks").mkdir(parents=True)
    (out / "weeks" / f"{nxt}_UFC-Next-Night.html").write_text("left from an earlier build")
    site.build(state, out)
    index = (out / "index.html").read_text()
    assert "UFC Test Night" in index and "Picks locked" in index and "UFC Next Night" not in index
    assert "UFC Next Night" not in (out / "record.html").read_text()
    assert not (out / "weeks" / f"{nxt}_UFC-Next-Night.html").exists()
    assert not (out / "data" / "weeks" / f"{nxt}_UFC-Next-Night.json").exists()
    assert nxt not in (out / "data" / "tracking.csv").read_text()
    assert len(site.read_tracking(state / "tracking.csv")) == 2  # the record itself is untouched
    # once the card has finished (8 hours after its first bout), the next card goes up within a day
    opens = site.next_card_from(started.isoformat(), week["locks_at"])
    assert pd.Timestamp(started) + pd.Timedelta(hours=8) < opens <= pd.Timestamp(started) + pd.Timedelta(hours=32)
    assert [w["event"] for w in site.shown_weeks(site.load_weeks(state), opens)] == ["UFC Test Night", "UFC Next Night"]


def test_next_card_goes_up_on_monday_morning_brisbane_time():
    """Oct 2026: the owner expected next week's card on Monday morning (Brisbane) and it came that evening."""
    def opens(lock, next_start="2026-12-31T00:00:00+00:00"):
        return site.next_card_from(lock, next_start).tz_convert("Australia/Brisbane").strftime("%a %d %b %H:%M")
    assert opens("2026-10-17T21:10:00+00:00") == "Mon 19 Oct 06:00"  # Saturday night in North America
    assert opens("2026-10-24T14:00:00+00:00") == "Mon 26 Oct 06:00"  # Saturday evening in Abu Dhabi
    assert opens("2026-01-31T23:00:00+00:00") == "Mon 02 Feb 06:00"  # Sunday morning in Sydney
    assert opens("2026-11-21T05:00:00+00:00") == "Sun 22 Nov 23:00"  # Asia: a day after it ends is sooner
    assert opens("2026-10-18T22:00:00+00:00") == "Tue 20 Oct 16:00"  # a Sunday card ends after Monday morning
    # back-to-back events: never later than a day before the next card's first bout
    assert site.next_card_from("2026-10-17T21:00:00+00:00", "2026-10-18T21:00:00+00:00") == pd.Timestamp(
        "2026-10-17T21:00:00+00:00")


def test_publishing_holds_the_next_card_and_fight_day_refreshes_the_current_one(tmp_path, monkeypatch):
    state, out = tmp_path / "state", tmp_path / "site"
    (state / "weeks").mkdir(parents=True)
    current = dict(_week("2026-10-17", "2026-10-17T21:10:00+00:00", BOUTS), slug="2026-10-17_UFC-Test")
    (state / "weeks" / "2026-10-17_UFC-Test.json").write_text(json.dumps(current))
    monkeypatch.setattr(site, "_bouts", lambda picks: [dict(BOUTS[0])])
    monkeypatch.setattr(site.report_mod, "render", lambda *a, **k: None)
    monkeypatch.setattr(site.tr, "picks_to_rows", lambda picks, require_odds=False: [])
    predict = lambda card, name, when: (pd.DataFrame({"Fighter A": card["fighter_a"]}), None)
    card = pd.DataFrame({"fighter_a": ["Alexander Volkanovski"], "fighter_b": ["Movsar Evloev"]})
    day, start = pd.Timestamp("2026-10-24"), pd.Timestamp("2026-10-24T12:00:00Z")
    for now in ("2026-10-17T21:44:00Z", "2026-10-18T19:59:00Z"):  # during the card, and up to Monday 6am Brisbane
        held = site._publish(card, "UFC 333", day, start, "ufcstats.com", state, out, pd.Timestamp(now), [], predict)
        assert held is None and len(site.load_weeks(state)) == 1
    up = site._publish(card, "UFC 333", day, start, "ufcstats.com", state, out,
                       pd.Timestamp("2026-10-18T20:00:00Z"), [], predict)
    assert up["event"] == "UFC 333" and len(site.load_weeks(state)) == 2
    # on fight day, before this week's card locks, a later card from ufcstats.com is not the one to refresh
    assert site._pending_card(state, "2026-10-24", pd.Timestamp("2026-10-17T18:00:00Z"))["slug"] == "2026-10-17_UFC-Test"
    assert site._pending_card(state, "2026-10-24", pd.Timestamp("2026-10-17T21:30:00Z")) is None  # locked: move on
    assert site._pending_card(state, "2026-10-18", pd.Timestamp("2026-10-17T18:00:00Z")) is None  # same event, a day off
