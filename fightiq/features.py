"""Point-in-time fighter stats and matchup features.

Every stat for a bout is computed only from that fighter's UFC bouts on earlier
dates, so the model never sees the result it is predicting or anything after it.
Upcoming bouts go through the same code path (with an unknown result), so
training and prediction features are built identically.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import ratings, records

# Pseudo-counts that pull small samples toward the league average. A fighter with
# one short UFC fight otherwise gets extreme rates (e.g. 100% takedown defence).
PRIOR_MINUTES = 15.0
PRIOR_STRIKES = 60.0
PRIOR_TAKEDOWNS = 6.0
PRIOR_FIGHTS = 2.0

ELO_K = 64.0
ELO_START = 1500.0
DEFAULT_LAYOFF_DAYS = 365.0
DEFAULT_AGE = 30.0

FEATURES_CLASSIC = ["slpm", "exp_str_acc", "sapm", "td_avg", "exp_td_acc", "sub_avg", "reach", "age", "win_pct"]
# Opponent-adjusted output: what a fighter did in each bout minus what that opponent usually allowed (or did), per
# minute, weighted by recency (half-life ADJ_HALF_LIFE years). A takedown on a fighter who is never taken down counts
# for more than one on a fighter who is taken down three times a fight.
ADJ_HALF_LIFE = 1.0
ADJ = ["adj_slpm", "adj_sapm", "adj_td", "adj_tdd", "adj_ctrl", "adj_ctrld", "adj_acc"]
# Production feature set. Win %, recent form, sub avg, KD avg and TD accuracy added nothing once a rating was in the
# model. Glicko (rating + its uncertainty) replaced Elo and the opponent-adjusted stats were added in Oct 2026: the
# walk-forward log loss of the stats model improved by about 0.005 on both 2015-2020 and 2021-2026.
# Pro records outside the UFC (Sherdog) followed: about 0.003 more on both periods (fightiq/records.py).
FEATURES_FULL = ["slpm", "exp_str_acc", "sapm", "td_avg", "reach", "age", "glicko", "glicko_rd",
                 "ctrl_avg", "experience", "layoff", "age_old", "debut"] + ADJ + records.FEATURES

_SUM_COLS = ["minutes", "sig_l", "sig_a", "td_l", "td_a", "kd", "sub_att", "ctrl",
             "opp_sig_l", "opp_sig_a", "opp_td_l", "opp_td_a", "fights", "wins", "losses"]


def _long_table(fights: pd.DataFrame, rounds: pd.DataFrame) -> pd.DataFrame:
    """One row per fighter per bout with own and opponent totals."""
    stat_cols = ["sig_l", "sig_a", "td_l", "td_a", "kd", "sub_att", "ctrl"]
    st = rounds.set_index(["fight_id", "side"])[stat_cols]
    rows = []
    for side, opp in (("a", "b"), ("b", "a")):
        df = fights[["fight_id", "date", "bout_seq", "seconds", "result_a"]].copy()
        df["fighter_id"] = fights[f"id_{side}"]
        df["opp_id"] = fights[f"id_{opp}"]
        df["side"] = side
        res = fights["result_a"] if side == "a" else 1 - fights["result_a"]
        df["won"] = (res == 1).astype(float)
        df["lost"] = (res == 0).astype(float)
        df["fights"] = 1.0
        own = st.reindex(pd.MultiIndex.from_arrays([fights["fight_id"], [side] * len(fights)]))
        oth = st.reindex(pd.MultiIndex.from_arrays([fights["fight_id"], [opp] * len(fights)]))
        for c in stat_cols:
            df[c] = own[c].to_numpy()
        for c in ["sig_l", "sig_a", "td_l", "td_a", "ctrl"]:
            df["opp_" + c] = oth[c].to_numpy()
        has_stats = own["sig_a"].notna().to_numpy() & oth["sig_a"].notna().to_numpy()
        df["minutes"] = np.where(has_stats, df["seconds"] / 60.0, 0.0)
        df["opp_ctrl"] = np.where(has_stats, df["opp_ctrl"], 0.0)
        rows.append(df)
    long = pd.concat(rows, ignore_index=True)
    long = long.rename(columns={"won": "wins", "lost": "losses"})
    stat_all = stat_cols + ["opp_sig_l", "opp_sig_a", "opp_td_l", "opp_td_a", "opp_ctrl"]
    long[stat_all] = long[stat_all].fillna(0.0)
    return long


def _pre_fight_totals(long: pd.DataFrame) -> pd.DataFrame:
    """Career totals for each fighter strictly before each bout date."""
    day = long.groupby(["fighter_id", "date"])[_SUM_COLS].sum().sort_index()
    before = day.groupby(level=0).cumsum() - day
    before.columns = ["pre_" + c for c in before.columns]

    # last fight date before this date, and form over the previous 3 bouts
    d = day.reset_index()[["fighter_id", "date", "wins", "losses"]]
    d["prev_date"] = d.groupby("fighter_id")["date"].shift(1)
    decided = d["wins"] + d["losses"]
    d["day_score"] = d["wins"] + 0.5 * (decided == 0)  # draw / no contest counts as half
    d["day_n"] = decided.clip(lower=1)
    for c in ("day_score", "day_n"):
        d["last3_" + c] = d.groupby("fighter_id")[c].transform(lambda s: s.shift(1).rolling(3, min_periods=1).sum())
    d = d.set_index(["fighter_id", "date"])[["prev_date", "last3_day_score", "last3_day_n"]]
    return before.join(d)


def _elo(fights: pd.DataFrame, k: float = ELO_K) -> pd.DataFrame:
    """Pre-fight Elo for both corners, updated one fight-day at a time."""
    rating: dict[str, float] = {}
    pre_a = np.empty(len(fights))
    pre_b = np.empty(len(fights))
    f = fights.reset_index(drop=True)
    groups = f.groupby("date").indices
    for day in sorted(groups):
        idx = groups[day]
        updates: dict[str, float] = {}
        for i in idx:
            a, b, r = f.at[i, "id_a"], f.at[i, "id_b"], f.at[i, "result_a"]
            ra, rb = rating.get(a, ELO_START), rating.get(b, ELO_START)
            pre_a[i], pre_b[i] = ra, rb
            if r == r:  # skip no-contests and upcoming bouts
                exp_a = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
                updates[a] = updates.get(a, 0.0) + k * (r - exp_a)
                updates[b] = updates.get(b, 0.0) - k * (r - exp_a)
        for fid, delta in updates.items():
            rating[fid] = rating.get(fid, ELO_START) + delta
    return pd.DataFrame({"fight_id": f["fight_id"], "elo_a": pre_a, "elo_b": pre_b})


def _pre_decay(day: pd.DataFrame, half_life_years: float) -> pd.DataFrame:
    """Sum over each fighter's EARLIER fight days of x * 0.5 ** (years ago / half-life), at every fight day."""
    d = day.reset_index()
    t = (d["date"] - d.groupby("fighter_id")["date"].transform("min")).dt.days.to_numpy() / 365.25 / half_life_years
    up, down = np.power(2.0, t), np.power(2.0, -t)
    vals = day.to_numpy() * up[:, None]
    cs = pd.DataFrame(vals, index=day.index).groupby(level=0).cumsum().to_numpy() - vals
    return pd.DataFrame(cs * down[:, None], index=day.index, columns=day.columns)


def _opponent_adjusted(long: pd.DataFrame, priors: dict[str, float]) -> pd.DataFrame:
    """Per (fighter_id, date): recency-weighted output relative to what each opponent usually allowed/did."""
    cols = ["minutes", "sig_l", "sig_a", "td_l", "td_a", "ctrl", "opp_sig_l", "opp_sig_a", "opp_td_l", "opp_td_a",
            "opp_ctrl"]
    day = long.groupby(["fighter_id", "date"])[cols].sum().sort_index()
    pre = day.groupby(level=0).cumsum() - day  # each opponent's career rates going into the bout
    m = pre["minutes"] + PRIOR_MINUTES
    td_pm, ctrl_pm = priors["td_avg"] / 15, priors["ctrl_avg"] / 15
    base = pd.DataFrame({
        "slpm": (pre["sig_l"] + priors["slpm"] * PRIOR_MINUTES) / m,
        "sapm": (pre["opp_sig_l"] + priors["slpm"] * PRIOR_MINUTES) / m,
        "td_pm": (pre["td_l"] + td_pm * PRIOR_MINUTES) / m,
        "ctrl_pm": (pre["ctrl"] / 60 + ctrl_pm * PRIOR_MINUTES) / m,
        "td_allow": (pre["opp_td_l"] + td_pm * PRIOR_MINUTES) / m,
        "ctrl_allow": (pre["opp_ctrl"] / 60 + ctrl_pm * PRIOR_MINUTES) / m,
        "str_def": 1 - (pre["opp_sig_l"] + priors["str_acc"] * PRIOR_STRIKES) / (pre["opp_sig_a"] + PRIOR_STRIKES),
    }, index=pre.index)
    o = base.reindex(pd.MultiIndex.from_arrays([long["opp_id"], long["date"]]))
    mins = long["minutes"].to_numpy()
    res = pd.DataFrame({
        "fighter_id": long["fighter_id"].to_numpy(), "date": long["date"].to_numpy(), "minutes": mins,
        "r_sig_l": long["sig_l"].to_numpy() - o["sapm"].to_numpy() * mins,
        "r_opp_sig_l": long["opp_sig_l"].to_numpy() - o["slpm"].to_numpy() * mins,
        "r_td_l": long["td_l"].to_numpy() - o["td_allow"].to_numpy() * mins,
        "r_opp_td_l": long["opp_td_l"].to_numpy() - o["td_pm"].to_numpy() * mins,
        "r_ctrl": long["ctrl"].to_numpy() / 60 - o["ctrl_allow"].to_numpy() * mins,
        "r_opp_ctrl": long["opp_ctrl"].to_numpy() / 60 - o["ctrl_pm"].to_numpy() * mins,
        "sig_a": long["sig_a"].to_numpy(),
        "r_acc": long["sig_l"].to_numpy() - long["sig_a"].to_numpy() * (1 - o["str_def"].to_numpy()),
    }).fillna(0.0)
    dd = res.groupby(["fighter_id", "date"]).sum().sort_index()
    P = _pre_decay(dd, ADJ_HALF_LIFE)
    m = P["minutes"] + PRIOR_MINUTES
    return pd.DataFrame({
        "adj_slpm": P["r_sig_l"] / m, "adj_sapm": P["r_opp_sig_l"] / m,
        "adj_td": P["r_td_l"] / m * 15, "adj_tdd": P["r_opp_td_l"] / m * 15,
        "adj_ctrl": P["r_ctrl"] / m * 15, "adj_ctrld": P["r_opp_ctrl"] / m * 15,
        "adj_acc": P["r_acc"] / (P["sig_a"] + PRIOR_STRIKES),
    }, index=dd.index)


def league_priors(rounds: pd.DataFrame, fights: pd.DataFrame) -> dict[str, float]:
    """League-average rates used as the shrinkage target."""
    minutes = fights.loc[fights["fight_id"].isin(rounds["fight_id"]), "seconds"].sum() / 60.0 * 2
    t = rounds[["sig_l", "sig_a", "td_l", "td_a", "kd", "sub_att", "ctrl"]].sum()
    return {
        "slpm": t["sig_l"] / minutes,
        "str_acc": t["sig_l"] / t["sig_a"],
        "td_avg": t["td_l"] / minutes * 15,
        "td_acc": t["td_l"] / t["td_a"],
        "kd_avg": t["kd"] / minutes * 15,
        "sub_avg": t["sub_att"] / minutes * 15,
        "ctrl_avg": t["ctrl"] / 60.0 / minutes * 15,
    }


def _fighter_stats(pre: pd.DataFrame, priors: dict[str, float]) -> pd.DataFrame:
    """Shrunk career rates in ufcstats units from pre-fight totals."""
    m = pre["pre_minutes"] + PRIOR_MINUTES
    out = pd.DataFrame(index=pre.index)
    out["slpm"] = (pre["pre_sig_l"] + priors["slpm"] * PRIOR_MINUTES) / m
    out["sapm"] = (pre["pre_opp_sig_l"] + priors["slpm"] * PRIOR_MINUTES) / m
    out["str_acc"] = (pre["pre_sig_l"] + priors["str_acc"] * PRIOR_STRIKES) / (pre["pre_sig_a"] + PRIOR_STRIKES)
    out["str_def"] = 1 - (pre["pre_opp_sig_l"] + priors["str_acc"] * PRIOR_STRIKES) / (pre["pre_opp_sig_a"] + PRIOR_STRIKES)
    out["td_avg"] = (pre["pre_td_l"] + priors["td_avg"] / 15 * PRIOR_MINUTES) / m * 15
    out["td_acc"] = (pre["pre_td_l"] + priors["td_acc"] * PRIOR_TAKEDOWNS) / (pre["pre_td_a"] + PRIOR_TAKEDOWNS)
    out["td_def"] = 1 - (pre["pre_opp_td_l"] + priors["td_acc"] * PRIOR_TAKEDOWNS) / (pre["pre_opp_td_a"] + PRIOR_TAKEDOWNS)
    out["sub_avg"] = (pre["pre_sub_att"] + priors["sub_avg"] / 15 * PRIOR_MINUTES) / m * 15
    out["kd_avg"] = (pre["pre_kd"] + priors["kd_avg"] / 15 * PRIOR_MINUTES) / m * 15
    out["ctrl_avg"] = (pre["pre_ctrl"] / 60.0 + priors["ctrl_avg"] / 15 * PRIOR_MINUTES) / m * 15
    out["win_pct"] = (pre["pre_wins"] + PRIOR_FIGHTS / 2) / (pre["pre_wins"] + pre["pre_losses"] + PRIOR_FIGHTS)
    out["ufc_fights"] = pre["pre_fights"]
    out["ufc_wins"] = pre["pre_wins"]
    out["ufc_losses"] = pre["pre_losses"]
    out["ufc_minutes"] = pre["pre_minutes"]
    out["recent_form"] = (pre["last3_day_score"].fillna(0) + 0.5) / (pre["last3_day_n"].fillna(0) + 1)
    out["prev_date"] = pre["prev_date"]
    return out


def build(fights: pd.DataFrame, rounds: pd.DataFrame, fighters: pd.DataFrame,
          upcoming: pd.DataFrame | None = None, priors: dict[str, float] | None = None) -> pd.DataFrame:
    """Return one row per bout with each corner's pre-fight profile and the A-minus-B features.

    `upcoming` takes the same columns as `fights` (fight_id, date, id_a, id_b, ...) with
    result_a = NaN; those rows come back with features built from all completed bouts.
    """
    allf = fights.copy()
    allf["upcoming"] = False
    if upcoming is not None and len(upcoming):
        up = upcoming.copy()
        up["upcoming"] = True
        up["result_a"] = np.nan
        up["seconds"] = np.nan
        allf = pd.concat([allf, up], ignore_index=True)
    allf = allf.sort_values(["date"], kind="stable").reset_index(drop=True)
    allf["bout_seq"] = np.arange(len(allf))
    priors = priors or league_priors(rounds, fights)

    long = _long_table(allf, rounds)
    # upcoming bouts must not add to anyone's totals
    up_mask = long["fight_id"].isin(allf.loc[allf["upcoming"], "fight_id"])
    long.loc[up_mask, _SUM_COLS + ["opp_ctrl"]] = 0.0
    pre = _pre_fight_totals(long)
    stats = _fighter_stats(pre, priors).join(_opponent_adjusted(long, priors))

    out = allf.merge(_elo(allf), on="fight_id", how="left").merge(ratings.glicko(allf), on="fight_id", how="left")
    attrs = fighters.set_index("fighter_id")[["reach", "height", "dob", "stance"]]
    for s in ("a", "b"):
        st = stats.reindex(pd.MultiIndex.from_arrays([out[f"id_{s}"], out["date"]]))
        for c in st.columns:
            out[f"{c}_{s}"] = st[c].to_numpy()
        at = attrs.reindex(out[f"id_{s}"])
        out[f"reach_{s}"] = at["reach"].to_numpy()
        out[f"height_{s}"] = at["height"].to_numpy()
        out[f"stance_{s}"] = at["stance"].to_numpy()
        out[f"age_{s}"] = (out["date"] - pd.to_datetime(at["dob"].to_numpy())).dt.days / 365.25
        out[f"layoff_{s}"] = (out["date"] - out[f"prev_date_{s}"]).dt.days
    _impute_attributes(out)
    done = allf[~allf["upcoming"]]
    first_ufc = pd.concat([done[["id_a", "date"]].set_axis(["fid", "date"], axis=1),
                           done[["id_b", "date"]].set_axis(["fid", "date"], axis=1)]).groupby("fid")["date"].min()
    pro = records.features(out, first_ufc)
    out = add_matchup_features(out)
    return pd.concat([out, pro], axis=1)


def _impute_attributes(out: pd.DataFrame) -> None:
    for s in ("a", "b"):
        # reach missing on many older fighters: estimate from height, else division average
        out[f"reach_{s}"] = out[f"reach_{s}"].fillna(out[f"height_{s}"])
        div_reach = out.groupby("division")[f"reach_{s}"].transform("median")
        out[f"reach_{s}"] = out[f"reach_{s}"].fillna(div_reach).fillna(72.0)
        out[f"age_{s}"] = out[f"age_{s}"].fillna(DEFAULT_AGE)
        out[f"layoff_{s}"] = out[f"layoff_{s}"].fillna(DEFAULT_LAYOFF_DAYS)


def add_matchup_features(out: pd.DataFrame) -> pd.DataFrame:
    """A-minus-B difference features (antisymmetric: swapping corners flips the sign)."""
    a = lambda c: out[f"{c}_a"]
    b = lambda c: out[f"{c}_b"]
    feats = pd.DataFrame(index=out.index)
    feats["slpm"] = a("slpm") - b("slpm")
    feats["sapm"] = a("sapm") - b("sapm")
    feats["exp_str_acc"] = np.sqrt(a("str_acc") * (1 - b("str_def"))) - np.sqrt(b("str_acc") * (1 - a("str_def")))
    feats["td_avg"] = a("td_avg") - b("td_avg")
    feats["exp_td_acc"] = np.sqrt(a("td_acc") * (1 - b("td_def"))) - np.sqrt(b("td_acc") * (1 - a("td_def")))
    feats["sub_avg"] = a("sub_avg") - b("sub_avg")
    feats["kd_avg"] = a("kd_avg") - b("kd_avg")
    feats["ctrl_avg"] = a("ctrl_avg") - b("ctrl_avg")
    feats["reach"] = a("reach") - b("reach")
    feats["age"] = a("age") - b("age")
    feats["age_old"] = np.maximum(a("age") - 33, 0) - np.maximum(b("age") - 33, 0)
    feats["win_pct"] = a("win_pct") - b("win_pct")
    feats["elo"] = (out["elo_a"] - out["elo_b"]) / 100.0
    feats["glicko"] = out["glicko_lg"]  # the ratings' own log-odds that A wins
    feats["glicko_rd"] = (out["glicko_rd_a"] - out["glicko_rd_b"]) / 100.0
    for c in ADJ:
        feats[c] = (a(c) - b(c)).fillna(0.0)
    feats["experience"] = np.log1p(a("ufc_fights")) - np.log1p(b("ufc_fights"))
    feats["layoff"] = np.log1p(a("layoff").clip(lower=0)) - np.log1p(b("layoff").clip(lower=0))
    feats["recent_form"] = a("recent_form") - b("recent_form")
    feats["debut"] = (a("ufc_fights") == 0).astype(float) - (b("ufc_fights") == 0).astype(float)
    return pd.concat([out, feats.add_prefix("f_")], axis=1)


def current_profiles(fights: pd.DataFrame, rounds: pd.DataFrame, fighters: pd.DataFrame,
                     priors: dict[str, float], as_of: pd.Timestamp) -> pd.DataFrame:
    """Every fighter's profile going into a bout on `as_of`, via the same path as predictions.

    Each fighter gets a placeholder upcoming bout against an opponent with no history, so the
    numbers are exactly the ones `build` would use for a real upcoming bout on that date.
    """
    ids = pd.unique(pd.concat([fights["id_a"], fights["id_b"]]))
    up = pd.DataFrame({
        "fight_id": [f"profile:{i}" for i in range(len(ids))], "event": "", "bout": "", "date": as_of,
        "name_a": "", "name_b": "", "id_a": ids, "id_b": "__no_history__", "division": "other",
        "women": False, "title": False, "scheduled_rounds": np.nan, "method": "",
    })
    X = build(fights, rounds, fighters, upcoming=up, priors=priors)
    X = X[X["upcoming"]]
    cols = [c[:-2] for c in X.columns if c.endswith("_a") and c not in ("id_a", "name_a", "result_a")]
    out = X[["id_a"] + [c + "_a" for c in cols]].copy()
    out.columns = ["fighter_id"] + cols
    return out.reset_index(drop=True)
