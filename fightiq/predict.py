"""Predict a card, write the picks spreadsheet, and grade past picks."""
from __future__ import annotations

import difflib
import re
from datetime import datetime

import numpy as np
import pandas as pd

from . import config, data, features, records
from . import evaluate as ev
from .model import FightModel

KELLY_FRACTION = 0.25
MAX_STAKE = 0.02  # never suggest more than 2% of bankroll on one bout

LABELS = {
    "slpm": "strikes landed/min", "exp_str_acc": "striking accuracy vs opponent's defence",
    "sapm": "strikes absorbed/min", "td_avg": "takedowns/15 min", "reach": "reach", "age": "age",
    "elo": "Elo (results vs quality of opposition)", "ctrl_avg": "control time", "experience": "UFC experience",
    "glicko": "rating (results vs quality of opposition)", "glicko_rd": "rating uncertainty",
    "adj_slpm": "strikes landed vs what opponents allow", "adj_sapm": "strikes absorbed vs opponents' usual output",
    "adj_td": "takedowns vs what opponents allow", "adj_tdd": "takedowns conceded vs opponents' usual",
    "adj_ctrl": "control vs what opponents allow", "adj_ctrld": "control conceded vs opponents' usual",
    "adj_acc": "striking accuracy vs opponents' defence",
    "pro_win_pct": "record outside the UFC", "pro_fights": "pro experience", "pro_years": "years as a pro",
    "layoff": "time since last fight", "age_old": "age past 33", "debut": "UFC debut",
    "win_pct": "UFC win %", "exp_td_acc": "takedown accuracy vs opponent's defence", "sub_avg": "sub attempts",
    "kd_avg": "knockdowns", "recent_form": "recent form",
}
LOG_PATH = config.OUTPUT_DIR / "predictions_log.csv"


def _history_names(fights: pd.DataFrame) -> dict[str, str]:
    """Normalised name -> fighter id, for everyone with at least one UFC bout."""
    names = pd.concat([fights[["date", "name_a", "id_a"]].set_axis(["date", "name", "id"], axis=1),
                       fights[["date", "name_b", "id_b"]].set_axis(["date", "name", "id"], axis=1)])
    names = names.sort_values("date")
    return {data.norm_name(n): i for n, i in zip(names["name"], names["id"])}


def resolve_card(card: pd.DataFrame, fights: pd.DataFrame, fighters: pd.DataFrame) -> pd.DataFrame:
    known = set(fights["id_a"]) | set(fights["id_b"])
    by_name = _history_names(fights)
    resolver = data.FighterResolver(fighters)
    card = card.copy()
    notes = [[] for _ in range(len(card))]
    for s in ("a", "b"):
        ids = []
        for i, row in card.iterrows():
            name = row[f"fighter_{s}"]
            url = row.get(f"url_{s}", "") or ""
            div = data.parse_division(row.get("weight_class", ""))[0]
            tagged = re.match(r"(.*?)\s*\(([A-Za-z ]+)\)(?:\s*#\d+)?$", str(name))
            if tagged:  # Fighters-sheet spelling for shared names, e.g. "Bruno Silva (Flyweight)"
                name, div = tagged.group(1), data.parse_division(tagged.group(2))[0]
            limit = data.WEIGHT_LIMITS.get(div, np.nan)
            fid = url if url in known else resolver.resolve(name, limit)
            if fid not in known:
                close = difflib.get_close_matches(data.norm_name(name), list(by_name), n=1, cutoff=0.85)
                if close:
                    fid = by_name[close[0]]
                    notes[i].append(f"matched '{name}' to '{close[0].title()}'")
                else:
                    fid = url or fid
                    notes[i].append(f"{name}: no UFC history (debut)")
            ids.append(fid)
        card[f"id_{s}"] = ids
    card["notes"] = ["; ".join(n) for n in notes]
    return card


def predict_card(card: pd.DataFrame, event_name: str = "", event_date: datetime | None = None,
                 model_path=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (picks, fighter_profiles) for a card with fighter_a/fighter_b (+ optional odds)."""
    model = FightModel.load(model_path or config.MODEL_PATH)
    priors = model.meta.get("priors")
    blend_w = model.meta.get("blend_weights", {})
    w = np.array([blend_w.get("market_logit", 1.0), blend_w.get("model_score", 0.0)])
    threshold = model.meta.get("bet_threshold", 0.05)

    fights, rounds = data.load_fights()
    fighters = data.load_fighters()
    card = resolve_card(card, fights, fighters)
    date = pd.Timestamp(event_date or datetime.now()).normalize()
    records.ensure(card, date)  # pro records outside the UFC for fighters new to the data (debutants)

    division = card.get("weight_class", pd.Series([""] * len(card))).map(data.parse_division)
    up = pd.DataFrame({
        "fight_id": [f"upcoming:{i}" for i in range(len(card))],
        "event": event_name, "bout": card["fighter_a"] + " vs. " + card["fighter_b"], "date": date,
        "name_a": card["fighter_a"], "name_b": card["fighter_b"], "id_a": card["id_a"], "id_b": card["id_b"],
        "division": [d[0] for d in division], "women": [d[1] for d in division], "title": [d[2] for d in division],
        "scheduled_rounds": np.nan, "method": "",
    })
    X = features.build(fights, rounds, fighters, upcoming=up, priors=priors)
    X = X[X["upcoming"]].set_index("fight_id").loc[up["fight_id"]].reset_index()

    p_model = model.predict_proba(X)
    contrib = model.contributions(X)
    odds_a = card["odds_a"].to_numpy(float) if "odds_a" in card else np.full(len(card), np.nan)
    odds_b = card["odds_b"].to_numpy(float) if "odds_b" in card else np.full(len(card), np.nan)
    # decimal odds must be above 1; anything else is a typo (or American odds typed as decimal) and counts as missing
    odds_a, odds_b = np.where(odds_a > 1, odds_a, np.nan), np.where(odds_b > 1, odds_b, np.nan)
    has_odds = ~np.isnan(odds_a) & ~np.isnan(odds_b)
    p_market = np.where(has_odds, ev.devig(np.where(has_odds, odds_a, 2), np.where(has_odds, odds_b, 2)), np.nan)
    p_final = np.where(has_odds, ev.blend_proba(np.where(has_odds, p_market, 0.5), model.score(X), w), p_model)

    rows = []
    for i in range(len(card)):
        a, b = card.at[i, "fighter_a"], card.at[i, "fighter_b"]
        pa = p_final[i]
        pick, pick_p = (a, pa) if pa >= 0.5 else (b, 1 - pa)
        row = {
            "Bout": f"{a} vs {b}", "Weight class": card.get("weight_class", pd.Series([""] * len(card)))[i],
            "Fighter A": a, "Fighter B": b,
            "Model P(A)": p_model[i], "Market P(A)": p_market[i], "FightIQ P(A)": pa,
            "Pick": pick, "Pick probability": pick_p, "Odds A": odds_a[i], "Odds B": odds_b[i],
            "Bet": "", "Bet odds": np.nan, "EV": np.nan, "Stake (% bankroll)": np.nan,
            "Key factors": _key_factors(contrib.iloc[i], a, b),
            "Notes": card.at[i, "notes"],
            "_id_a": card.at[i, "id_a"], "_id_b": card.at[i, "id_b"],
        }
        if has_odds[i]:
            ev_a, ev_b = pa * odds_a[i] - 1, (1 - pa) * odds_b[i] - 1
            side, e, o, p = (a, ev_a, odds_a[i], pa) if ev_a >= ev_b else (b, ev_b, odds_b[i], 1 - pa)
            row["EV"] = e
            debut = X.at[i, "ufc_fights_a"] == 0 or X.at[i, "ufc_fights_b"] == 0
            if e > threshold and debut:
                row["Notes"] = (row["Notes"] + "; no bet on debut bouts").strip("; ")
            elif e > threshold:
                kelly = (p * o - 1) / (o - 1)
                row.update({"Bet": side, "Bet odds": o,
                            "Stake (% bankroll)": min(KELLY_FRACTION * kelly, MAX_STAKE)})
        rows.append(row)
    picks = pd.DataFrame(rows)
    picks.insert(0, "Event", event_name)
    picks.insert(1, "Date", date.date())
    return picks, _profiles(X)


def _key_factors(contrib: pd.Series, a: str, b: str, k: int = 3) -> str:
    top = contrib.reindex(contrib.abs().sort_values(ascending=False).index)[:k]
    parts = []
    for feat, val in top.items():
        if abs(val) < 0.05:
            continue
        who = a if val > 0 else b
        parts.append(f"{LABELS.get(feat, feat)} favours {who.split()[-1]} ({abs(val):.2f})")
    return "; ".join(parts)


def _profiles(X: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for s in ("a", "b"):
        for _, r in X.iterrows():
            rows.append({
                "Fighter": r[f"name_{s}"], "Bout": r["bout"],
                "UFC record": f"{int(r[f'ufc_wins_{s}'])}-{int(r[f'ufc_losses_{s}'])}",
                "UFC fights": int(r[f"ufc_fights_{s}"]), "UFC minutes": round(r[f"ufc_minutes_{s}"], 1),
                "SLpM": r[f"slpm_{s}"], "Str Acc": r[f"str_acc_{s}"], "SApM": r[f"sapm_{s}"],
                "Str Def": r[f"str_def_{s}"], "TD Avg": r[f"td_avg_{s}"], "TD Acc": r[f"td_acc_{s}"],
                "TD Def": r[f"td_def_{s}"], "Sub Avg": r[f"sub_avg_{s}"], "Ctrl min/15": r[f"ctrl_avg_{s}"],
                "Elo": round(r[f"elo_{s}"]), "Age": round(r[f"age_{s}"], 1), "Reach": r[f"reach_{s}"],
                "Days since last fight": None if r[f"ufc_fights_{s}"] == 0 else int(r[f"layoff_{s}"]),
            })
    return pd.DataFrame(rows).sort_values("Bout", kind="stable")


def write_outputs(picks: pd.DataFrame, profiles: pd.DataFrame, event_name: str) -> tuple:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = "".join(ch if ch.isalnum() else "_" for ch in (event_name or "card")).strip("_")[:60]
    xlsx = config.OUTPUT_DIR / f"{picks['Date'].iloc[0]}_{slug}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        picks.drop(columns=["_id_a", "_id_b"]).to_excel(xw, sheet_name="Picks", index=False)
        profiles.to_excel(xw, sheet_name="Fighter stats (pre-fight)", index=False)
        _format_sheets(xw)
    _append_log(picks)
    return xlsx, LOG_PATH


def _format_sheets(xw) -> None:
    pct = {"Model P(A)", "Market P(A)", "FightIQ P(A)", "Pick probability", "EV", "Stake (% bankroll)",
           "Str Acc", "Str Def", "TD Acc", "TD Def"}
    two_dp = {"Odds A", "Odds B", "Bet odds", "SLpM", "SApM", "TD Avg", "Sub Avg", "Ctrl min/15"}
    for ws in xw.book.worksheets:
        headers = [c.value for c in ws[1]]
        for j, h in enumerate(headers, start=1):
            letter = ws.cell(1, j).column_letter
            width = max(len(str(h)), *(len(str(c.value or "")) for c in ws[letter][1:])) if ws.max_row > 1 else len(str(h))
            ws.column_dimensions[letter].width = min(max(10, width + 2), 60)
            fmt = "0.0%" if h in pct else "0.00" if h in two_dp else None
            if fmt:
                for c in ws[letter][1:]:
                    c.number_format = fmt
        ws.freeze_panes = "A2"


def _append_log(picks: pd.DataFrame) -> None:
    log = picks[["Event", "Date", "Fighter A", "Fighter B", "Model P(A)", "Market P(A)", "FightIQ P(A)",
                 "Pick", "Odds A", "Odds B", "Bet", "Bet odds", "EV", "Stake (% bankroll)", "_id_a", "_id_b"]].copy()
    log.insert(0, "Logged at", datetime.now().isoformat(timespec="minutes"))
    for c in ("Winner", "Pick correct", "Bet profit (units)"):
        log[c] = np.nan
    if LOG_PATH.exists():
        old = pd.read_csv(LOG_PATH)
        # re-running a card replaces its earlier unsettled rows
        key = lambda d: d["Date"].astype(str) + "|" + d["Fighter A"] + "|" + d["Fighter B"]
        old = old[~(key(old).isin(key(log)) & old["Winner"].isna())]
        log = pd.concat([old, log], ignore_index=True)
    log.to_csv(LOG_PATH, index=False)


def grade(log_path=LOG_PATH) -> pd.DataFrame:
    """Fill in results for logged picks from the latest data and print the record."""
    log = pd.read_csv(log_path, dtype={"Winner": object, "Bet": object, "Pick": object})
    fights, _ = data.load_fights()
    fights = fights[fights["result_a"].notna()]
    by_pair = {}
    for r in fights.itertuples():
        # winner recorded as which corner won, matched by fighter id or by name
        winner = {1.0: "a", 0.0: "b"}.get(r.result_a, "draw")
        for pa, pb in ((r.id_a, r.id_b), (data.norm_name(r.name_a), data.norm_name(r.name_b))):
            by_pair.setdefault((pa, pb), []).append((r.date, winner))
            by_pair.setdefault((pb, pa), []).append((r.date, {"a": "b", "b": "a"}.get(winner, winner)))
    for i, r in log[log["Winner"].isna()].iterrows():
        d = pd.Timestamp(r["Date"])
        hits = []
        for pair in ((r["_id_a"], r["_id_b"]), (data.norm_name(r["Fighter A"]), data.norm_name(r["Fighter B"]))):
            hits = [w for (fd, w) in by_pair.get(pair, []) if abs((fd - d).days) <= 7]
            if hits:
                break
        if not hits:
            continue
        log.at[i, "Winner"] = {"a": r["Fighter A"], "b": r["Fighter B"]}.get(hits[0], "Draw")
        log.at[i, "Pick correct"] = float(log.at[i, "Winner"] == r["Pick"])
        if isinstance(r["Bet"], str) and r["Bet"]:
            log.at[i, "Bet profit (units)"] = (r["Bet odds"] - 1) if log.at[i, "Winner"] == r["Bet"] else -1.0
    log.to_csv(log_path, index=False)
    settled = log[log["Winner"].notna() & (log["Winner"] != "Draw")]
    bets = settled[settled["Bet profit (units)"].notna()]
    print(f"picks: {int(settled['Pick correct'].sum())}-{int((1 - settled['Pick correct']).sum())} "
          f"({settled['Pick correct'].mean():.1%})" if len(settled) else "no settled picks yet")
    if len(bets):
        print(f"bets: {len(bets)}, profit {bets['Bet profit (units)'].sum():+.2f}u, "
              f"ROI {bets['Bet profit (units)'].mean():+.1%}")
    return log
