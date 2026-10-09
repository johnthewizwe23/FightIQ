# FightIQ

UFC fight predictions built only from what was known **before** each fight.

FightIQ downloads every UFC bout's round-by-round stats (UFC 1 to the most recent event), rebuilds each
fighter's ufcstats-style career numbers (SLpM, Str Acc, SApM, Str Def, TD Avg, ...) as they stood the
day before every fight, and fits a logistic regression on the differences between the two fighters. When you
give it betting odds it blends the model with the market price and flags bets with positive expected value.

No more copying stats off ufcstats.com by hand: one command predicts the next card.

## Setup

Python 3.10 or newer.

```bash
pip install -r requirements.txt
python -m fightiq train      # downloads the data, backtests, fits the model (~10 seconds)
```

## Weekly workflow

```bash
python -m fightiq events                      # list upcoming UFC events
python -m fightiq predict                     # predict the next event (scraped from ufcstats.com)
python -m fightiq predict --odds odds.csv     # ...with odds, so it can suggest bets
python -m fightiq grade                       # after the event: settle the picks, print record and ROI
```

If ufcstats.com can't be reached, or you want to enter the card yourself, use a CSV:

```bash
python -m fightiq predict --card my_card.csv --event "UFC 333" --date 2026-10-17
```

```csv
fighter_a,fighter_b,odds_a,odds_b,weight_class
Natalia Silva,Wang Cong,1.45,2.85,Women's Flyweight
Ateba Gautier,Roman Kopylov,-250,+200,Middleweight
```

Odds can be decimal (`1.45`) or American (`-250`, `+200`), and they're optional. `weight_class` is optional
and only matters for the few fighters who share a name. See `examples/card_example.csv` for a template; its
odds are illustrative, not real lines. Fighter order doesn't matter: the model is symmetric.

Each run writes `output/<date>_<event>.xlsx` with two sheets:

* **Picks**: model probability, market probability (bookmaker margin removed), FightIQ probability
  (the blend), pick, expected value, suggested bet and stake, and the key factors behind each pick.
* **Fighter stats (pre-fight)**: every fighter's UFC record and career stats going into the fight.

Every prediction is also appended to `output/predictions_log.csv`, and `grade` fills in the results. That
log is your live track record.

Other commands: `python -m fightiq update` forces a fresh data download (`predict` refreshes data older than
12 hours by itself). `python -m fightiq fighter "Payton Talbott"` prints one fighter's current stats.
Re-run `train` every month or so to refit on the newest fights.

## How it works

1. **Data**: [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats), a daily-refreshed
   scrape of ufcstats.com (every event, bout result and round-by-round stat line, plus fighter height, reach
   and date of birth). The files are downloaded at runtime into `data/raw/`; they aren't stored in this repo.
2. **Point-in-time stats** (`fightiq/features.py`): for each bout, each fighter's totals are summed over
   their UFC bouts on *earlier dates only*. Small samples are pulled toward the league average, so one
   short fight can't give someone 100% takedown defence. Age is computed at fight date. An Elo rating is
   updated fight by fight. Tests check that adding later fights never changes an earlier fight's features.
3. **Features**: A-minus-B differences of strikes landed/absorbed per minute, expected striking accuracy
   √(my accuracy × (1 − opponent's defence)) (kept from the original spreadsheet), takedowns per 15 min,
   reach, age, age past 33, Elo, control time, UFC experience, layoff and a debut flag.
4. **Model** (`fightiq/model.py`): logistic regression trained on every bout from both corners with no
   intercept, so P(A beats B) = 1 − P(B beats A) and entry order can't bias the number.
5. **Blend**: logit(p) = a × logit(market) + b × model score, with a and b fitted on out-of-sample
   predictions. A bet is suggested when the blend gives a side more than 5% expected value, never on a
   bout with a UFC debutant. Stake is ¼ Kelly, capped at 2% of bankroll.

## Backtest

Full details are in [`reports/evaluation.md`](reports/evaluation.md). Everything there is walk-forward:
each year is predicted by a model trained only on earlier years.

* The stats model alone picks **~62%** of winners from 2015 to 2026, which matches the old spreadsheet's live
  record of 62.2%. The betting market picks ~66%. Stats alone don't beat the bookies.
* The **blend beats the market price** on log loss, and the betting rule above returned **+14% ROI on 599
  bets** from 2016 to 2026 (95% interval +4% to +24%), profitable in 10 of 11 years.
* Caveat: the historical odds come from a public dataset
  ([shortlikeafox/ultimate_ufc_dataset](https://github.com/shortlikeafox/ultimate_ufc_dataset)) that doesn't
  say whether they are opening, closing or best-available prices. If they're openers, real returns will be
  lower. Log the odds you actually get, compare them with the closing line, and size small until the live
  record agrees.

## Project layout

```
fightiq/
  data.py       download + clean ufcstats data and historical odds
  features.py   point-in-time fighter stats, Elo, matchup features
  model.py      symmetric logistic regression (saved as JSON in models/)
  evaluate.py   walk-forward backtest, market blend, betting simulation
  pipeline.py   train command: backtest, fit, write reports/evaluation.md
  card.py       scrape upcoming cards from ufcstats.com, or read a card CSV
  predict.py    predict a card, write the spreadsheet, log and grade picks
  cli.py        command line entry point (python -m fightiq ...)
tests/          pytest suite (python -m pytest)
```
