"""Command line: python -m fightiq <command>."""
from __future__ import annotations

import argparse
from datetime import datetime

import pandas as pd
import requests

from . import card as card_mod
from . import config, data


def cmd_update(args):
    data.download(force=True)


def cmd_train(args):
    from . import pipeline
    data.download()
    pipeline.train()


def _scrape(fn, *a):
    try:
        return fn(*a)
    except requests.RequestException as exc:
        raise SystemExit(f"couldn't reach ufcstats.com ({exc}).\n"
                         "Use a card CSV instead: python -m fightiq predict --card my_card.csv") from exc


def cmd_events(args):
    for i, e in enumerate(_scrape(card_mod.list_upcoming)):
        print(f"[{i}] {e['date']:%Y-%m-%d}  {e['name']}" if e["date"] else f"[{i}] {e['name']}")


def cmd_predict(args):
    from .predict import predict_card, write_outputs
    data.download(max_age_hours=12)
    if not config.MODEL_PATH.exists():
        raise SystemExit("no trained model yet - run: python -m fightiq train")
    if args.card:
        card = card_mod.read_card_csv(args.card)
        name = args.event or "Custom card"
        date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else None
    else:
        card, name, date = _scrape(card_mod.scrape_upcoming, args.event_index)
        if args.odds:
            card = _merge_odds(card, card_mod.read_card_csv(args.odds))
    picks, profiles = predict_card(card, name, date)
    xlsx, log = write_outputs(picks, profiles, name)
    _print_picks(picks, name)
    print(f"\nspreadsheet: {xlsx}\nlogged to:   {log}")


def _merge_odds(card: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    """Attach odds from a CSV (fighter_a, fighter_b, odds_a, odds_b) to a scraped card, in either corner order."""
    card = card.copy()
    card["odds_a"] = float("nan")
    card["odds_b"] = float("nan")
    for i, r in card.iterrows():
        a, b = data.norm_name(r["fighter_a"]), data.norm_name(r["fighter_b"])
        for _, o in odds.iterrows():
            oa, ob = data.norm_name(o["fighter_a"]), data.norm_name(o["fighter_b"])
            if (oa, ob) == (a, b):
                card.loc[i, ["odds_a", "odds_b"]] = [o["odds_a"], o["odds_b"]]
            elif (oa, ob) == (b, a):
                card.loc[i, ["odds_a", "odds_b"]] = [o["odds_b"], o["odds_a"]]
    return card


def _print_picks(picks: pd.DataFrame, name: str) -> None:
    print(f"\n{name}  ({picks['Date'].iloc[0]})\n")
    for _, r in picks.iterrows():
        line = f"{r['Bout']:<48} pick {r['Pick']:<24} {r['Pick probability']:6.1%}"
        if r["Bet"]:
            line += f"   BET {r['Bet']} @ {r['Bet odds']:.2f} (EV {r['EV']:+.1%}, stake {r['Stake (% bankroll)']:.1%})"
        print(line)
        if r["Notes"]:
            print(f"{'':48}  note: {r['Notes']}")


def cmd_grade(args):
    from .predict import LOG_PATH, grade
    data.download(force=True)
    if not LOG_PATH.exists():
        raise SystemExit("nothing logged yet - run predict first")
    grade(LOG_PATH)


def cmd_fighter(args):
    from .predict import predict_card
    card = pd.DataFrame({"fighter_a": [args.name], "fighter_b": [args.name]})
    data.download(max_age_hours=12)
    _, profiles = predict_card(card, "profile")
    print(profiles.iloc[0].drop("Bout").to_string())


def main(argv=None):
    p = argparse.ArgumentParser(prog="fightiq", description="UFC fight predictions from point-in-time ufcstats data")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update", help="re-download the latest data").set_defaults(func=cmd_update)
    sub.add_parser("train", help="backtest and fit the model, write reports/evaluation.md").set_defaults(func=cmd_train)
    sub.add_parser("events", help="list upcoming UFC events on ufcstats.com").set_defaults(func=cmd_events)
    pr = sub.add_parser("predict", help="predict the next card (scraped) or a card CSV")
    pr.add_argument("--card", help="CSV with fighter_a, fighter_b[, odds_a, odds_b, weight_class]")
    pr.add_argument("--odds", help="CSV of odds to attach to a scraped card")
    pr.add_argument("--event-index", type=int, default=0, help="0 = next event, 1 = the one after, ...")
    pr.add_argument("--event", help="event name (with --card)")
    pr.add_argument("--date", help="event date YYYY-MM-DD (with --card)")
    pr.set_defaults(func=cmd_predict)
    sub.add_parser("grade", help="settle logged picks against results").set_defaults(func=cmd_grade)
    fp = sub.add_parser("fighter", help="show a fighter's current stats")
    fp.add_argument("name")
    fp.set_defaults(func=cmd_fighter)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
