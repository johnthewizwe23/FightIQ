"""Train the model, run the walk-forward backtest and write the evaluation report."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import config, data, features
from . import evaluate as ev
from .model import FightModel

C = 1.0
BET_THRESHOLD = 0.05  # bet when the blended probability gives >5% expected value


def build_history(priors: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    fights, rounds = data.load_fights()
    fighters = data.load_fighters()
    priors = priors or features.league_priors(rounds, fights)
    X = features.build(fights, rounds, fighters, priors=priors)
    return X, fights, rounds, fighters, priors


def train(report_path=None, model_path=None) -> FightModel:
    report_path = report_path or config.ROOT / "reports" / "evaluation.md"
    model_path = model_path or config.MODEL_PATH
    X, fights, rounds, fighters, priors = build_history()
    X = ev.attach_odds(X, data.load_odds())

    print("walk-forward backtest (train on earlier years, test on the next)...")
    P = ev.walk_forward_blend(ev.walk_forward(X, features.FEATURES_FULL, C))
    P_classic = ev.walk_forward(X, features.FEATURES_CLASSIC, C)
    P_elo = ev.walk_forward(X, ["elo"], C)

    final = FightModel(features.FEATURES_FULL, C).fit(X[X["date"] >= ev.TRAIN_START])
    blend_w = ev.fit_blend(P[P["p_market"].notna()])
    summary = _write_report(report_path, X, P, P_classic, P_elo, final, blend_w)
    _write_backtest_json(report_path.parent / "backtest.json", X, P, P_classic, P_elo, final, blend_w)
    final.save(model_path, extra={"blend_weights": {"market_logit": float(blend_w[0]), "model_score": float(blend_w[1])},
                                  "bet_threshold": BET_THRESHOLD, "priors": priors, "backtest": summary})
    print(f"model saved to {model_path}\nreport written to {report_path}")
    return final


def _fmt_scores(name: str, s: dict) -> str:
    return f"| {name} | {s['n']:,} | {s['accuracy']:.1%} | {s['log_loss']:.4f} | {s['brier']:.4f} |"


def _write_report(path, X, P, P_classic, P_elo, final, blend_w) -> dict:
    mk = P[P["p_market"].notna() & P["p_blend"].notna()]
    years = f"{int(P['year'].min())}–{int(P['year'].max())}"
    lines = [
        "# FightIQ evaluation",
        "",
        f"Generated {date.today().isoformat()} from {len(X):,} UFC bouts "
        f"({X['date'].min().date()} to {X['date'].max().date()}).",
        "",
        "Every number below is **out of sample**: for each test year the model was trained only on "
        "bouts from earlier years, and every fighter stat was computed only from bouts before that fight date.",
        "",
        f"## Prediction quality, {years}",
        "",
        "| Model | Bouts | Accuracy | Log loss | Brier |",
        "|---|---|---|---|---|",
        _fmt_scores("FightIQ model (stats only)", ev.scores(P["result_a"], P["p_model"])),
        _fmt_scores("Your original 9 features, rebuilt point-in-time", ev.scores(P_classic["result_a"], P_classic["p_model"])),
        _fmt_scores("Elo rating only", ev.scores(P_elo["result_a"], P_elo["p_model"])),
        "",
        f"Bouts with historical odds ({len(mk):,} bouts, years where the blend had at least one prior year to learn from):",
        "",
        "| Model | Bouts | Accuracy | Log loss | Brier |",
        "|---|---|---|---|---|",
        _fmt_scores("FightIQ model (stats only)", ev.scores(mk["result_a"], mk["p_model"])),
        _fmt_scores("Bookmaker odds (margin removed)", ev.scores(mk["result_a"], mk["p_market"])),
        _fmt_scores("**Blend: odds + FightIQ model**", ev.scores(mk["result_a"], mk["p_blend"])),
        "",
        "Lower log loss / Brier is better. The stats model alone is weaker than the betting market; "
        "the blend starts from the market price and adjusts it using the model.",
        "",
        "## Calibration (favoured side)",
        "",
    ]
    for name, col, d in (("Stats model", "p_model", P), ("Blend", "p_blend", mk)):
        cal = ev.calibration(d["result_a"], d[col])
        lines += [f"**{name}**", "", "| Predicted | Bouts | Avg predicted | Actually won |", "|---|---|---|---|"]
        lines += [f"| {r.predicted} | {r.fights:,} | {r.avg_predicted:.1%} | {r.actual_win_rate:.1%} |" for r in cal.itertuples()]
        lines.append("")

    no_debut = mk[(mk["ufc_fights_a"] > 0) & (mk["ufc_fights_b"] > 0)]
    lines += ["## Betting backtest (flat 1 unit, settled at the historical odds)", "",
              "The rule used by `predict`: back a side when the blend gives it more than "
              f"{BET_THRESHOLD:.0%} expected value, and never bet a bout involving a UFC debutant "
              "(the model has no stats for them, and in the backtest it added nothing on those bouts).", "",
              "| Strategy | Bets | Profit (units) | ROI |", "|---|---|---|---|"]

    def sim_row(label, d, col, t):
        sim = ev.betting_sim(d, col, t)
        b = sim[sim["bet"]]
        lines.append(f"| {label} | {len(b):,} | {b['pnl'].sum():+.1f} | {b['pnl'].mean():+.1%} |")
        return sim, b

    for t in ev.BET_THRESHOLDS:
        sim_row(f"Stats model alone, EV > {t:.0%}", no_debut, "p_model", t)
    for t in ev.BET_THRESHOLDS:
        sim, b = sim_row(f"Blend, EV > {t:.0%}", no_debut, "p_blend", t)
        if t == BET_THRESHOLD:
            main_sim, main_bets = sim, b
    sim_row(f"Blend, EV > {BET_THRESHOLD:.0%}, debut bouts only (not bet)",
            mk[(mk["ufc_fights_a"] == 0) | (mk["ufc_fights_b"] == 0)], "p_blend", BET_THRESHOLD)
    fav = np.where(no_debut["odds_a"] < no_debut["odds_b"],
                   np.where(no_debut["result_a"] == 1, no_debut["odds_a"] - 1, -1),
                   np.where(no_debut["result_a"] == 0, no_debut["odds_b"] - 1, -1))
    lines.append(f"| Back every favourite | {len(no_debut):,} | {fav.sum():+.1f} | {fav.mean():+.1%} |")

    lo, hi = ev.event_bootstrap_roi(main_sim)
    summary = {"bets": int(len(main_bets)), "profit_units": float(main_bets["pnl"].sum()),
               "roi": float(main_bets["pnl"].mean()), "roi_95ci": [lo, hi]}
    lines += ["", f"Rule as used by `predict`: 95% interval for ROI (resampling whole cards) {lo:+.1%} to {hi:+.1%}.",
              "", "By year:", "", "| Year | Bets | Profit | ROI |", "|---|---|---|---|"]
    per_year = main_bets.groupby("year")["pnl"].agg(["size", "sum", "mean"])
    lines += [f"| {y} | {int(r['size'])} | {r['sum']:+.1f} | {r['mean']:+.1%} |" for y, r in per_year.iterrows()]
    lines += ["", "By the odds of the side backed:", "", "| Odds | Bets | ROI |", "|---|---|---|"]
    for lo_o, hi_o in ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 100.0)):
        m = (main_bets["odds"] >= lo_o) & (main_bets["odds"] < hi_o)
        lines.append(f"| {lo_o:.1f}–{hi_o:.1f} | {int(m.sum())} | {main_bets.loc[m, 'pnl'].mean():+.1%} |")
    lines += [
        "",
        "**Caveats.** The historical odds are closing prices: for each fighter the middle of the closing range across "
        "bookmakers on bestfightodds.com (`odds_history/bestfightodds.csv`), then the odds FightIQ published when each "
        "card locked, then a public dataset for anything missing. Bets are settled at that middle price; the best "
        "price on the day is usually better, the price earlier in the week can be worse. Treat this as evidence worth "
        "testing live, not as a guaranteed edge: the Record page compares every suggested bet with the closing line.",
        "",
        "## Final model",
        "",
        f"Trained on {final.meta['trained_fights']:,} bouts ({final.meta['trained_from']} to {final.meta['trained_to']}). "
        "Coefficients are per unit of each A-minus-B feature; *impact* is the log-odds shift for a one-standard-deviation difference.",
        "",
        "| Feature | Coefficient | Impact (1 sd) |", "|---|---|---|",
    ]
    sd = np.sqrt((X[["f_" + f for f in final.features]] ** 2).mean()).to_numpy()
    for f, c, s in sorted(zip(final.features, final.coef, sd), key=lambda t: -abs(t[1] * t[2])):
        lines.append(f"| {f} | {c:+.4f} | {c * s:+.3f} |")
    lines += ["", f"Blend: logit(p) = {blend_w[0]:.3f} × logit(market) + {blend_w[1]:.3f} × model score.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return summary


def _write_backtest_json(path, X, P, P_classic, P_elo, final, blend_w) -> None:
    """The backtest numbers in a form the website can show."""
    import json

    mk = P[P["p_market"].notna() & P["p_blend"].notna()]
    no_debut = mk[(mk["ufc_fights_a"] > 0) & (mk["ufc_fights_b"] > 0)]

    def sim(d, col, t):
        b = ev.betting_sim(d, col, t)
        return b, b[b["bet"]]

    strategies = []
    for col, label in (("p_model", "Stats model alone"), ("p_blend", "FightIQ blend")):
        for t in ev.BET_THRESHOLDS:
            _, b = sim(no_debut, col, t)
            strategies.append({"strategy": label, "threshold": t, "bets": int(len(b)),
                               "profit": float(b["pnl"].sum()), "roi": float(b["pnl"].mean())})
    fav = np.where(no_debut["odds_a"] < no_debut["odds_b"],
                   np.where(no_debut["result_a"] == 1, no_debut["odds_a"] - 1, -1),
                   np.where(no_debut["result_a"] == 0, no_debut["odds_b"] - 1, -1))
    strategies.append({"strategy": "Back every favourite", "threshold": None, "bets": int(len(fav)),
                       "profit": float(fav.sum()), "roi": float(fav.mean())})
    rule_sim, rule = sim(no_debut, "p_blend", BET_THRESHOLD)
    lo, hi = ev.event_bootstrap_roi(rule_sim)
    per_year = [{"year": int(y), "bets": int(g.size), "profit": float(g.sum()), "roi": float(g.mean())}
                for y, g in rule.groupby("year")["pnl"]]
    bands = []
    for lo_o, hi_o in ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 100.0)):
        m = (rule["odds"] >= lo_o) & (rule["odds"] < hi_o)
        bands.append({"odds": f"{lo_o:.1f}-{hi_o:.1f}" if hi_o < 100 else f"{lo_o:.1f}+",
                      "bets": int(m.sum()), "roi": float(rule.loc[m, "pnl"].mean()) if m.any() else None})
    sd = np.sqrt((X[["f_" + f for f in final.features]] ** 2).mean()).to_numpy()
    out = {
        "generated": date.today().isoformat(),
        "data": {"bouts": int(len(X)), "from": str(X["date"].min().date()), "to": str(X["date"].max().date())},
        "test_years": [int(P["year"].min()), int(P["year"].max())],
        "quality_all": [
            {"model": "FightIQ stats model", **ev.scores(P["result_a"], P["p_model"])},
            {"model": "Original 9 features (rebuilt)", **ev.scores(P_classic["result_a"], P_classic["p_model"])},
            {"model": "Elo rating only", **ev.scores(P_elo["result_a"], P_elo["p_model"])},
        ],
        "quality_odds": [
            {"model": "FightIQ stats model", **ev.scores(mk["result_a"], mk["p_model"])},
            {"model": "Bookmaker odds", **ev.scores(mk["result_a"], mk["p_market"])},
            {"model": "FightIQ blend", **ev.scores(mk["result_a"], mk["p_blend"])},
        ],
        "strategies": strategies,
        "rule": {"threshold": BET_THRESHOLD, "bets": int(len(rule)), "profit": float(rule["pnl"].sum()),
                 "roi": float(rule["pnl"].mean()), "roi_95ci": [lo, hi]},
        "per_year": per_year,
        "odds_bands": bands,
        "calibration": ev.calibration(mk["result_a"], mk["p_blend"]).to_dict("records"),
        "features": [{"feature": f, "coef": float(c), "impact": float(c * s)}
                     for f, c, s in zip(final.features, final.coef, sd)],
        "blend": {"market_logit": float(blend_w[0]), "model_score": float(blend_w[1])},
        "trained": final.meta,
    }
    path.write_text(json.dumps(out, indent=2))
