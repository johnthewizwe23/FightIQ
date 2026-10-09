import math

from fightiq import data
from fightiq.card import parse_odds


def test_parse_of():
    assert data.parse_of("16 of 40") == (16.0, 40.0)
    assert all(math.isnan(x) for x in data.parse_of("---"))


def test_parse_clock():
    assert data.parse_clock("4:32") == 272
    assert math.isnan(data.parse_clock("--"))


def test_fight_seconds():
    assert data.fight_seconds(2, "1:30", "3 Rnd (5-5-5)") == 390
    assert data.fight_seconds(5, "5:00", "5 Rnd (5-5-5-5-5)") == 1500
    assert data.fight_seconds(1, "12:13", "No Time Limit") == 733
    assert data.fight_seconds(2, "2:00", "1 Rnd + OT (12-3)") == 840


def test_parse_division():
    assert data.parse_division("UFC Women's Flyweight Title Bout") == ("flyweight", True, True)
    assert data.parse_division("Light Heavyweight Bout") == ("light heavyweight", False, False)
    assert data.parse_division("Catch Weight Bout")[0] == "other"


def test_norm_name():
    assert data.norm_name("José Aldo") == "jose aldo"
    assert data.norm_name("Sean O'Malley") == "sean o malley"


def test_parse_odds():
    assert parse_odds("+150") == 2.5
    assert parse_odds("-200") == 1.5
    assert parse_odds("1.65") == 1.65
    assert parse_odds("150") == 2.5
    assert math.isnan(parse_odds(""))


def test_resolver_splits_name_collisions_by_weight():
    import pandas as pd
    fighters = pd.DataFrame({"fighter_id": ["u1", "u2"], "name": ["Bruno Silva"] * 2, "weight": [185.0, 125.0],
                             "key": ["bruno silva"] * 2})
    r = data.FighterResolver(fighters)
    assert r.resolve("Bruno Silva", 125) == "u2"
    assert r.resolve("Bruno Silva", 185) == "u1"
    assert r.resolve("Nobody Here").startswith("name:")
