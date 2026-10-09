# Review of the original spreadsheet model

Summary of the review of `FIGHTIQ_Model_CC_Review.xlsx` (Fight Database + Picks sheets), which led to
this rebuild.

## What was right

* The logistic regression itself was correct. Refitting it independently on rows 846+ reproduces the
  Solver coefficients to 3 decimals, and Solver was maximising log-likelihood, which is the right objective.
* The "expected strike accuracy" feature √(accuracy × (1 − opponent's defence)) is a good matchup
  interaction. It is kept in the new model.
* Live picks were tracked with real odds and losses included, which is what made an honest evaluation possible.

## What went wrong

1. **Look-ahead leakage.** The Kaggle stats were a single snapshot taken at scrape time. All 1,774 fighters
   who appear more than once had identical stats in every row, so a 2015 bout was "predicted" with career
   numbers that included that bout's result and everything after it. Win/loss ratio, the biggest weight
   (5.69), was the most affected. The model learned weights that were too steep and was overconfident live:
   picks averaged a stated 72% but won 62%.
2. **Unrepresentative test set.** Rows 0–845 (the 10% test set) came from a differently-sourced chunk where
   fighter 1 won ~90% of bouts (vs ~57% elsewhere). "Always pick fighter 1" scored ~90% there, so the
   headline 74.5% was below a do-nothing baseline.
3. **Didn't beat the market live.** 209 settled picks: 130–79 (62.2%), −15.7 units at a flat 1 unit
   (−7.5% ROI, 95% interval −18% to +3%). 85% of picks were the bookmaker favourite anyway. The bigger the
   edge the model claimed, the further it fell short of its own number. Brier score 0.245 for the model
   vs 0.203 for the de-vigged odds.
4. **Spreadsheet bug.** In 36 rows of the Picks sheet (rows ~63–117), fighter 2's TD control used fighter 1's
   columns (`=AA*AB*(1-AC)` instead of `=AL*AM*(1-AN)`). It moved probabilities by ~1 point and didn't
   change any pick.
5. **Minor.** The aggression score's negative weight was a correction for its 0.80 correlation with
   expected strike accuracy, not a real effect. Sub average wasn't significant. TD control used the
   fighter's own TD defence rather than the opponent's.

## How the rebuild addresses it

| Problem | Fix |
|---|---|
| Snapshot stats | Stats rebuilt from round-by-round data using only earlier bouts, with a test that proves it |
| Order / red-corner bias | Symmetric model: every bout trained from both corners, no intercept |
| Random 90/10 split on a mixed dataset | Walk-forward backtest, 2015–2026, train on earlier years only |
| Accuracy as the target | Log loss, calibration and ROI against historical odds |
| Model vs market | Model is blended with the market price, and bets need >5% expected value |
| Weekly manual data pull | `python -m fightiq predict` scrapes the card and computes everything |
