# FightIQ

UFC fight-prediction model. Python package in `fightiq/`; the user works mainly from the Excel workbook
`output/FightIQ_Model.xlsx` and the PDF reports in `output/reports/`. The user is not a developer: explain
in plain English and keep answers short.

## Weekly routine (what "run this week" means)

```bash
python3 -m fightiq weekly          # next card from ufcstats, live odds, Tracking + results, PDF report
```

Then tell the user the PDF path (`output/reports/FightIQ_<date>_<event>.pdf`) and summarise the value bets
from the command output or by reading the PDF/workbook. Other commands:

| Command | Use |
|---|---|
| `python3 -m fightiq workbook --refresh-odds` | Keep the card, update odds, rebuild workbook + PDF |
| `python3 -m fightiq workbook` | Keep the card; log odds the user typed into Tracking; fill results |
| `python3 -m fightiq report` | Rebuild the PDF from the saved workbook |
| `python3 -m fightiq workbook --event-html FILE` | Card from an event page saved from Chrome (if ufcstats blocks the scraper) |
| `python3 -m fightiq schedule` / `--status` / `--off` | Weekly automatic run on this Mac (launchd, Thursday 18:00 by default) |
| `python3 -m fightiq site` / `--render-only` | Update + rebuild the public website in `site/` (state in `site_data/`) |
| `python3 -m fightiq train` | Refit the model (monthly) and rewrite `reports/evaluation.md` + `reports/backtest.json` |
| `python3 -m pytest -q` | Tests |

## Rules

- The workbook must be saved and closed in Excel before any command that rebuilds it; otherwise the user's
  unsaved odds are lost or Excel overwrites the new file. Rebuilds keep Tracking rows and back up the old file
  to `output/backup/`.
- Never commit `output/`, `data/` or `.odds_api_key` (all git-ignored). The odds key is the user's private
  The Odds API key.
- Odds come from The Odds API (region `au`, best price across bookmakers). Bouts it doesn't list stay blank
  for the user to type in. Historical odds for training: `odds_history/bestfightodds.csv` (closing ranges), then
  each locked card's published odds, then the public ufc-master dataset.
- The Excel Predictor no longer recomputes the model: Python writes each bout's model score; Excel still
  recalculates the market, blend, EV and stake when odds are typed in.
- ufcstats.com shows scripts a JavaScript "checking your browser" page; `fightiq/card.py` falls back to
  driving Google Chrome with Playwright. If that fails, use the saved-page route above.
- Don't change the model or bet rules (5% EV threshold, quarter Kelly capped at 2%, no bets on UFC debuts)
  without re-running `python3 -m fightiq train` and checking `reports/evaluation.md`.

## Public website

`.github/workflows/fightiq-site.yml` runs `python3 -m fightiq site` daily on GitHub, commits `site/` and `site_data/`,
and deploys `site/` to GitHub Pages. Published picks are a public record: never edit or delete files in
`site_data/` by hand, and never change a card's picks after its `locks_at` time.
The next card is only published (and shown) a day after the current card finishes: `next_card_from` in `site.py`.

## Layout

`data.py` (download/clean ufcstats data, historical odds) · `features.py` (point-in-time stats, opponent-adjusted
stats) · `ratings.py` (Glicko) · `records.py` (Sherdog pro records outside the UFC, `records/`) · `model.py`
(symmetric logistic regression) · `evaluate.py` (walk-forward backtest, market blend) · `card.py`
(ufcstats scraper) · `odds.py` (The Odds API) · `predict.py` · `tracking.py` · `workbook.py` (Excel) ·
`report.py` (PDF) · `site.py` (website) · `history.py` (walk-forward pick for every past fight, the site's
"Past fights" search, written to `site_data/fight_history.json` on every site run) · `schedule.py` (launchd) · `cli.py`. User guide: `docs/FightIQ_Weekly_Guide.pdf`.
