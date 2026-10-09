import numpy as np
import pandas as pd

from fightiq import odds

EVENTS = [  # shape of The Odds API v4 /odds response (h2h, decimal)
    {"home_team": "Brendan Allen", "away_team": "Christian Leroy Duncan", "commence_time": "2026-10-11T00:00:00Z",
     "bookmakers": [
         {"key": "sportsbet", "title": "SportsBet", "markets": [{"key": "h2h", "outcomes": [
             {"name": "Brendan Allen", "price": 1.50}, {"name": "Christian Leroy Duncan", "price": 2.60}]}]},
         {"key": "tab", "title": "TAB", "markets": [{"key": "h2h", "outcomes": [
             {"name": "Brendan Allen", "price": 1.55}, {"name": "Christian Leroy Duncan", "price": 2.50}]}]},
     ]},
    {"home_team": "Jai Herbert", "away_team": "Matheus Camilo", "bookmakers": [
        {"key": "neds", "title": "Neds", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Jai Herbert", "price": 3.10}, {"name": "Matheus Camilo", "price": 1.36}]}]}]},
]


def test_best_prices_takes_the_highest_price_per_fighter():
    p = odds.best_prices(EVENTS).set_index("fighter_a")
    assert p.loc["Brendan Allen", "odds_a"] == 1.55 and p.loc["Brendan Allen", "book_a"] == "TAB"
    assert p.loc["Brendan Allen", "odds_b"] == 2.60 and p.loc["Brendan Allen", "book_b"] == "SportsBet"


def test_attach_matches_either_corner_order_and_keeps_unmatched_odds():
    card = pd.DataFrame({"fighter_a": ["Brendan Allen", "Matheus Camilo", "Some One"],
                         "fighter_b": ["Christian Leroy Duncan", "Jai Herbert", "Some Body"],
                         "odds_a": [np.nan, np.nan, 1.9], "odds_b": [np.nan, np.nan, 1.9]})
    out, n = odds.attach(card, odds.best_prices(EVENTS))
    assert n == 2
    assert list(out["odds_a"]) == [1.55, 1.36, 1.9] and list(out["odds_b"]) == [2.60, 3.10, 1.9]
    assert "TAB" in out.at[0, "odds_source"]


def test_similarity_handles_tags_and_middle_names():
    assert odds.similarity("Bruno Silva (Flyweight)", "Bruno Silva") == 1.0
    assert odds.similarity("Alex Pereira", "Alex Poatan Pereira") >= 0.9


def test_add_live_odds_without_key_leaves_card(monkeypatch):
    monkeypatch.setattr(odds, "load_key", lambda: None)
    card = pd.DataFrame({"fighter_a": ["A"], "fighter_b": ["B"]})
    assert odds.add_live_odds(card) is card
