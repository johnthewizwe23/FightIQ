# FightIQ

UFC fight predictions built only from what was known **before** each fight.

FightIQ downloads every UFC bout's round-by-round stats (UFC 1 to the most recent event), rebuilds each
fighter's ufcstats-style career numbers (SLpM, Str Acc, SApM, Str Def, TD Avg, ...) as they stood the
day before every fight, and fits a logistic regression on the differences between the two fighters. When you
give it betting odds it blends the model with the market price and flags bets with positive expected value.

No more copying stats off ufcstats.com by hand: one command predicts the next card.

## Setup (one time)

1. **Install Python 3.10+** from [python.org/downloads](https://www.python.org/downloads/). On Windows, tick
   **"Add python.exe to PATH"** on the first installer screen.
2. **Get the code with GitHub Desktop.** *Add → Clone Repository… → GitHub.com* tab → pick
   `johnthewizwe23/FightIQ` → *Clone*. (A repo created on github.com only appears in GitHub Desktop once
   you clone it.) If the code is still on the branch `claude/confident-euler-i3nj2j` rather than `main`,
   pick that branch from *Current Branch*.
3. **Open a terminal in the repo:** GitHub Desktop → *Repository → Open in Terminal*
   (or Finder: right-click the folder → *New Terminal at Folder*; Windows: type `cmd` in the folder's address bar).
4. **Install and train** (on a Mac type `python3` / `pip3`; on Windows `python` / `pip`):

```bash
pip3 install -r requirements.txt
python3 -m fightiq train      # downloads the data, backtests, fits the model (~10 seconds)
```

To get later updates to the code: GitHub Desktop → *Fetch origin* → *Pull origin*.

## Weekly workflow (Excel)

One-time: get a free odds key at [the-odds-api.com](https://the-odds-api.com) (500 requests a month; FightIQ
uses 1 per run) and save it:

```bash
python3 -m fightiq odds-key YOUR_KEY
```

Each fight week (close Excel first):

```bash
python3 -m fightiq workbook --upcoming
open output/FightIQ_Model.xlsx
```

That one command:

* refreshes every fighter's stats with the latest results,
* fills the **Predictor** with the next UFC card from ufcstats.com (via your Chrome if the site asks for a
  browser check),
* fills in the best current odds from Australian bookmakers (hover an odds cell to see which bookmaker),
* logs every bout that has odds into **Tracking** (pick, probability, suggested bet and stake), and
* fills in W/L for earlier bouts once their results are in the data.

Each run also writes a two-page PDF to `output/reports/` (model predictions for every bout, then the
value bets with odds, edge and stake). Make and open it on its own with `python3 -m fightiq report`, for
example after typing in odds yourself.

Bouts the odds service doesn't list yet stay blank: type those odds in yourself. To update the odds closer
to fight time without changing the card: `python3 -m fightiq workbook --refresh-odds`. Running `workbook`
with no options keeps the current card and just refreshes stats and Tracking.

Your **Tracking** rows (including any you typed yourself) are carried over to each new file, and the old file
is copied to `output/backup/` first.

Typing a card yourself works too: on the Predictor sheet set the event date (B3), type Fighter A and
Fighter B, and the odds. Names aren't case-sensitive (`jim miller` = `Jim Miller`), but the spelling must
match the **Fighters** sheet (plain letters, no accents). The Notes column tells you when a name isn't found.
Save, then run `python3 -m fightiq workbook` to log it into Tracking.

ufcstats.com shows scripts a "Checking your browser… requires JavaScript" page. When that happens FightIQ
loads the page in your Google Chrome instead (via the `playwright` package in `requirements.txt`; a Chrome
window may open for a few seconds). If that still fails, save the event page from your browser instead:
open the event on ufcstats.com, press Cmd+S (Ctrl+S on Windows), choose **Webpage, HTML Only**, then:

```bash
python3 -m fightiq workbook --event-html ~/Downloads/the-saved-page.html
```

Other ways to fill the Predictor:

```bash
python3 -m fightiq workbook --card my_card.csv --event "UFC 333" --date 2026-10-17
python3 -m fightiq workbook --upcoming --no-live-odds    # card only, you type the odds
```

| Sheet | What it is |
|---|---|
| Guide | How to use it, how the model works, backtest results |
| Predictor | The card and odds (yellow cells); Excel formulas run the model and suggest bets |
| Tracking | Log picks and bets; record, units and ROI calculate themselves |
| Fighters | Every UFC fighter's stats going into their next fight |
| Model | Coefficients, blend weights and bet rules the Predictor uses |
| Backtest | Walk-forward results from 2015 on, by year |
| Fight Database | Every bout since 2001 with both fighters' pre-fight stats, model probability and odds |

The Predictor's formulas reproduce the Python model exactly (except debut bouts, where Excel doesn't know
the newcomer's reach; those are never bet).

## Public website (fully automatic, free)

`.github/workflows/fightiq-site.yml` runs FightIQ on GitHub every day (more often on Fridays, Saturdays and after events)
and publishes a website with four pages: **This week** (picks, win chances, value bets, PDF), **Record**
(every pick and bet with results, units, ROI and a profit chart), **Past fights** (search any UFC fight since 1994
and see who the model would have picked, using only earlier fights, or browse every event with FightIQ's hit rate
and average confidence per card) and **How it works** (method, factors, backtest and caveats). Light theme by
default with a dark-mode switch; asset URLs carry a content hash so browsers always load the latest styles.

* The card comes from ufcstats.com when it can be read; otherwise from The Odds API feed (UFC bouts only).
* The next card goes up a day after the current card finishes, so the weekend's card stays on the home page while
  it is fought and its results come in.
* Picks refresh with the latest odds until the first bout starts, then lock and never change. Every update is
  committed to `site_data/` (one JSON per card + `tracking.csv`), so the history is public and timestamped.
* Results are filled in from the fight data once each event is over.
* **Past fights** reads `site_data/fight_history.json`, rebuilt on every run: each year's fights are predicted by
  a model trained only on earlier years (from 2015 on these are exactly the backtest's predictions and bets).

One-time setup on github.com:

1. Merge this code into the `main` branch (scheduled workflows only run from the default branch).
2. **Settings > General > Change visibility > Public** (free GitHub Pages needs a public repo).
3. **Settings > Secrets and variables > Actions > New repository secret**: name `ODDS_API_KEY`, value = your key.
4. **Settings > Pages > Build and deployment > Source: GitHub Actions**.
5. **Actions > FightIQ website > Run workflow** for the first run. The site appears at
   `https://<your-username>.github.io/FightIQ/`.

Build or preview it locally: `python3 -m fightiq site` (or `--render-only` to rebuild pages from saved state),
then open `site/index.html`.

## Fully automatic weekly run (Mac)

```bash
python3 -m fightiq schedule                 # every Thursday 18:00 (your Mac's time)
python3 -m fightiq schedule --day fri --time 19:30
python3 -m fightiq schedule --status        # when it runs + the last run's log
python3 -m fightiq schedule --off
```

Each scheduled run does `python3 -m fightiq weekly` (next card, live odds, Tracking, results, PDF) and shows
a Mac notification when it's done. The Mac must be on and logged in; if it was asleep, the run happens when it
wakes. Run it by hand any time with `python3 -m fightiq weekly`.

## Weekly workflow (command line only)

```bash
python3 -m fightiq events                      # list upcoming UFC events
python3 -m fightiq predict --odds odds.csv     # predict the next event, write output/<date>_<event>.xlsx
python3 -m fightiq grade                       # after the event: settle the picks, print record and ROI
```

If ufcstats.com can't be reached, or you want to enter the card yourself, use a CSV (with either
`predict` or `workbook`):

```bash
python3 -m fightiq predict --card my_card.csv --event "UFC 333" --date 2026-10-17
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

Other commands: `python3 -m fightiq update` forces a fresh data download (`predict` refreshes data older than
12 hours by itself). `python3 -m fightiq fighter "Payton Talbott"` prints one fighter's current stats.
Re-run `train` every month or so to refit on the newest fights.

## How it works

1. **Data**: [Greco1899/scrape_ufc_stats](https://github.com/Greco1899/scrape_ufc_stats), a daily-refreshed
   scrape of ufcstats.com (every event, bout result and round-by-round stat line, plus fighter height, reach
   and date of birth). The files are downloaded at runtime into `data/raw/`; they aren't stored in this repo.
   Two more sources are kept in the repo: every fighter's **pro record outside the UFC** from Sherdog
   (`records/`, new fighters looked up automatically when a card is predicted) and **closing odds** for every UFC
   bout since 2008 from bestfightodds.com (`odds_history/`).
2. **Point-in-time stats** (`fightiq/features.py`): for each bout, each fighter's totals are summed over
   their UFC bouts on *earlier dates only*. Small samples are pulled toward the league average, so one
   short fight can't give someone 100% takedown defence. Age is computed at fight date. A Glicko rating (finishes
   count fully, split decisions half, with an uncertainty that grows with time out) is updated fight by fight. Tests
   check that adding later fights never changes an earlier fight's features.
3. **Features** (23, all A minus B): strikes landed/absorbed per minute, expected striking accuracy
   √(my accuracy × (1 − opponent's defence)) (kept from the original spreadsheet), takedowns per 15 min,
   reach, age, age past 33, Glicko rating and its uncertainty, control time, UFC experience, layoff, a debut flag,
   seven **opponent-adjusted** stats (strikes, takedowns and control relative to what each opponent usually allows,
   last year weighted most) and three **pro-record** features (win rate outside the UFC, total pro fights, years as
   a pro).
4. **Model** (`fightiq/model.py`): logistic regression trained on every bout from both corners with no
   intercept, so P(A beats B) = 1 − P(B beats A) and entry order can't bias the number.
5. **Blend**: logit(p) = a × logit(market) + b × model score (market = bookmaker probability with the margin
   removed by the power method), with a and b fitted on out-of-sample
   predictions. A bet is suggested when the blend gives a side more than 5% expected value, never on a
   bout with a UFC debutant. Stake is ¼ Kelly, capped at 2% of bankroll.

## Backtest

Full details are in [`reports/evaluation.md`](reports/evaluation.md). Everything there is walk-forward:
each year is predicted by a model trained only on earlier years.

* The stats model alone picks **62.5%** of winners from 2015 to 2026 (log loss 0.6437). The betting market picks
  ~67%. Stats alone don't beat the bookies.
* The **blend beats the market price**: 67.7% of winners, log loss 0.6017 vs 0.6037 for the closing odds, and the
  betting rule above returned **+12.7% ROI on 1,090 bets** from 2016 to 2026 (95% interval +6% to +19%),
  profitable in all 11 years.
* October 2026 upgrade (each piece tested walk-forward, chosen on 2015-20 and confirmed once on 2021-26): Glicko
  instead of Elo, the opponent-adjusted stats and the pro records together improved the stats model's log loss by
  0.011 (2015-20) and 0.008 (2021-26), both outside bootstrap noise, and the blend by 0.002 on 2015-20 (level on
  2021-26). Tried and dropped: home advantage (home fighters won 49.8%), line movement, momentum, recency weighting,
  gradient boosting / forests / ensembles (no better than this single model).
* The bookmaker margin is removed with the **power method** (more of it comes off the longshot), and 25 garbled
  historical prices (two sides implying under 100% or over 115%) are dropped. The power method was fixed in advance and
  made both the market and the blended probabilities slightly but consistently more accurate in 2016-20 and 2021-26;
  the ROI change (+14% to +16%) is well within noise. The Excel Predictor uses the identical calculation.
* The website's Record page checks the **closing line**: each value bet's first posted odds against the last odds
  before the picks lock. Odds that usually shorten are the best early sign the edge is real.
* Historical odds are closing prices: the middle of each fighter's closing range across bookmakers
  (bestfightodds.com), then the odds FightIQ itself published at lock, then the public
  [ultimate_ufc_dataset](https://github.com/shortlikeafox/ultimate_ufc_dataset). Bets in the backtest settle at that
  middle price. Log the odds you actually get, compare them with the closing line, and size small until the live
  record agrees.

## Project layout

```
fightiq/
  data.py       download + clean ufcstats data and historical odds
  features.py   point-in-time fighter stats, opponent-adjusted stats, matchup features
  ratings.py    Glicko ratings (fight by fight, point in time)
  records.py    pro records outside the UFC (Sherdog), looked up for new fighters when a card is predicted
  model.py      symmetric logistic regression (saved as JSON in models/)
  evaluate.py   walk-forward backtest, market blend, betting simulation
  pipeline.py   train command: backtest, fit, write reports/evaluation.md
  card.py       scrape upcoming cards from ufcstats.com, or read a card CSV
  predict.py    predict a card, write the spreadsheet, log and grade picks
  tracking.py   log picks into Tracking and settle results
  odds.py       live odds from The Odds API, and the card from its feed
  workbook.py   the Excel workbook (Predictor, Tracking, Fighters, ...)
  report.py     the predictions + bets PDF
  site.py       the public website (this week, record, past fights, how it works)
  history.py    walk-forward pick for every past fight (the website's fight finder)
  schedule.py   weekly automatic run on a Mac
  cli.py        command line entry point (python -m fightiq ...)
tests/          pytest suite (python -m pytest)
```
