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
