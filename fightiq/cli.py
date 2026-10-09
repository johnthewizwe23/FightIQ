"""Command line: python -m fightiq <command>."""
from __future__ import annotations

import argparse
import subprocess
import sys
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
                         "Use a card CSV instead: python3 -m fightiq predict --card my_card.csv") from exc
    except card_mod.ScrapeError as exc:
        raise SystemExit(f"{exc}\nUse a card CSV instead: python3 -m fightiq predict --card my_card.csv") from exc


def cmd_events(args):
    for i, e in enumerate(_scrape(card_mod.list_upcoming)):
        print(f"[{i}] {e['date']:%Y-%m-%d}  {e['name']}" if e["date"] else f"[{i}] {e['name']}")


def cmd_predict(args):
    from .predict import predict_card, write_outputs
    data.download(max_age_hours=12)
    if not config.MODEL_PATH.exists():
        raise SystemExit("no trained model yet - run: python -m fightiq train")
    card, name, date = _get_card(args, scrape=True)
    if "odds_a" not in card or card["odds_a"].isna().all():
        from .odds import add_live_odds
        card = add_live_odds(card)
    picks, profiles = predict_card(card, name, date)
    xlsx, log = write_outputs(picks, profiles, name)
    _print_picks(picks, name)
    print(f"\nspreadsheet: {xlsx}\nlogged to:   {log}")


def _get_card(args, scrape: bool):
    """(card, event name, event date) from --card, or scraped from ufcstats when `scrape`."""
    if args.card:
        card = card_mod.read_card_csv(args.card)
        name = args.event or "Custom card"
        date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else None
    elif getattr(args, "event_html", None):
        try:
            card, name, date = card_mod.card_from_saved_page(args.event_html)
        except (OSError, card_mod.ScrapeError) as exc:
            raise SystemExit(str(exc)) from exc
    elif scrape:
        card, name, date = _scrape(card_mod.scrape_upcoming, args.event_index)
    else:
        return None, "", None
    if args.odds and not args.card:
        card = _merge_odds(card, card_mod.read_card_csv(args.odds))
    return card, name, date


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


def cmd_workbook(args):
    from .workbook import build_workbook
    data.download(max_age_hours=12)
    if not config.MODEL_PATH.exists():
        raise SystemExit("no trained model yet - run: python -m fightiq train")
    try:
        card, name, date = _get_card(args, scrape=args.upcoming)
    except SystemExit as exc:
        # still build the workbook so the stats are fresh; the card can be typed into the Predictor
        print(f"{exc}\n\nBuilding the workbook without a card - type the bouts into the Predictor sheet.\n")
        card, name, date = None, "", None
    if card is not None:
        print(f"filling the Predictor with {name} ({len(card)} bouts)")
    # live odds for a freshly loaded card, or for the kept card with --refresh-odds;
    # a card CSV keeps the odds typed into it unless --refresh-odds is given
    new_card = card is not None and not args.card
    live = not args.no_live_odds and (args.refresh_odds or new_card)
    path = build_workbook(card=card, event_name=name, event_date=date, live_odds=live)
    print(f"workbook written to {path}")
    _make_report(path)


def cmd_weekly(args):
    """The whole weekly routine: next card, odds, tracking, results, PDF, then a notification."""
    from argparse import Namespace

    from .schedule import notify
    from .workbook import build_workbook
    print(f"=== FightIQ weekly run {datetime.now():%Y-%m-%d %H:%M} ===")
    data.download(force=True)
    if not config.MODEL_PATH.exists():
        raise SystemExit("no trained model yet - run: python3 -m fightiq train")
    problem = ""
    try:
        card, name, date = _get_card(Namespace(card=None, event_html=None, odds=None, event_index=0), scrape=True)
        print(f"filling the Predictor with {name} ({len(card)} bouts)")
    except SystemExit as exc:
        problem = "couldn't load the new card from ufcstats.com"
        print(f"{exc}\n{problem} - keeping the card already in the workbook")
        card, name, date = None, "", None
    try:
        path = build_workbook(card=card, event_name=name, event_date=date, live_odds=True)
    except SystemExit as exc:
        notify("FightIQ", f"Weekly run failed: {exc}")
        raise
    print(f"workbook written to {path}")
    pdf = _make_report(path, show=not args.quiet)
    message = f"{name or 'Card'} ready" if not problem else f"Done, but {problem}"
    if pdf is not None:
        message += f" - report in {pdf.parent.name}/{pdf.name}"
    notify("FightIQ", message)
    print(message)


def cmd_site(args):
    from . import site
    if not args.render_only:
        if not config.MODEL_PATH.exists():
            raise SystemExit("no trained model yet - run: python3 -m fightiq train")
        site.update(scrape=not args.no_scrape)
    print(f"site built: {site.build()}")


def cmd_schedule(args):
    from . import schedule
    if args.off:
        print(schedule.remove())
    elif args.status:
        print(schedule.status())
    else:
        print(schedule.install(args.day, args.time))


def _make_report(xlsx_path, show=False):
    from .report import report_from_workbook
    try:
        pdf = report_from_workbook(xlsx_path)
    except SystemExit:
        return None  # no card in the Predictor yet
    print(f"report written to {pdf}")
    if show and sys.platform == "darwin":
        subprocess.run(["open", str(pdf)], check=False)
    return pdf


def cmd_report(args):
    data.download(max_age_hours=12)
    if _make_report(config.OUTPUT_DIR / "FightIQ_Model.xlsx", show=True) is None:
        raise SystemExit("no card in the workbook yet - run: python3 -m fightiq workbook --upcoming")


def cmd_odds_key(args):
    from . import odds
    try:
        odds.check_key(args.key)
    except odds.OddsError as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        print(f"couldn't check the key right now ({exc}); saving it anyway")
    odds.save_key(args.key)
    print(f"odds API key saved to {odds.KEY_FILE} - odds will now fill in automatically")


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
    pr.add_argument("--event-html", help="event page saved from ufcstats.com in your browser (instead of scraping)")
    pr.add_argument("--event-index", type=int, default=0, help="0 = next event, 1 = the one after, ...")
    pr.add_argument("--event", help="event name (with --card)")
    pr.add_argument("--date", help="event date YYYY-MM-DD (with --card)")
    pr.set_defaults(func=cmd_predict)
    sub.add_parser("grade", help="settle logged picks against results").set_defaults(func=cmd_grade)
    wp = sub.add_parser("workbook", help="build the Excel workbook (Predictor, Fighters, Backtest, ...)")
    wp.add_argument("--upcoming", action="store_true", help="fill the Predictor with the next card from ufcstats.com")
    wp.add_argument("--card", help="fill the Predictor from a card CSV instead")
    wp.add_argument("--odds", help="CSV of odds to attach to the scraped card")
    wp.add_argument("--event-html", help="event page saved from ufcstats.com in your browser (instead of scraping)")
    wp.add_argument("--event-index", type=int, default=0, help="0 = next event, 1 = the one after, ...")
    wp.add_argument("--event", help="event name (with --card)")
    wp.add_argument("--date", help="event date YYYY-MM-DD (with --card)")
    wp.add_argument("--refresh-odds", action="store_true", help="keep the current card and update its odds")
    wp.add_argument("--no-live-odds", action="store_true", help="don't fetch odds from The Odds API")
    wp.set_defaults(func=cmd_workbook)
    sub.add_parser("report", help="make the predictions + bets PDF from the workbook").set_defaults(func=cmd_report)
    wk = sub.add_parser("weekly", help="the whole weekly routine: card, odds, tracking, results, PDF")
    wk.add_argument("--quiet", action="store_true", help="don't open the PDF at the end")
    wk.set_defaults(func=cmd_weekly)
    st = sub.add_parser("site", help="update the public website (picks, record, how it works) in site/")
    st.add_argument("--render-only", action="store_true", help="rebuild the pages from saved state, no downloads")
    st.add_argument("--no-scrape", action="store_true", help="skip ufcstats.com and take the card from the odds feed")
    st.set_defaults(func=cmd_site)
    sp = sub.add_parser("schedule", help="run the weekly routine automatically on your Mac")
    sp.add_argument("--day", default="thu", help="mon, tue, wed, thu, fri, sat or sun (default thu)")
    sp.add_argument("--time", default="18:00", help="24-hour time on your Mac (default 18:00)")
    sp.add_argument("--off", action="store_true", help="stop the weekly schedule")
    sp.add_argument("--status", action="store_true", help="show the schedule and the last run's log")
    sp.set_defaults(func=cmd_schedule)
    kp = sub.add_parser("odds-key", help="save your free The Odds API key (the-odds-api.com)")
    kp.add_argument("key")
    kp.set_defaults(func=cmd_odds_key)
    fp = sub.add_parser("fighter", help="show a fighter's current stats")
    fp.add_argument("name")
    fp.set_defaults(func=cmd_fighter)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
