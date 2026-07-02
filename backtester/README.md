# Prosperity Quant Lab

A from-scratch **event-driven limit-order-book backtester** and
**market-making research framework**, built on real order-book data captured
during **IMC Prosperity 4**.

I competed in Prosperity 4 (reached Round 2). The competition gives you a live
exchange but only a black-box online evaluator. This project rebuilds the part
that actually teaches you something — a backtesting and research harness I
control end to end — so strategies can be measured honestly offline: correct
order matching, position limits, mark-to-market PnL, microstructure analytics,
and walk-forward parameter selection.

> **What is and isn't mine.** The matching *engine, metrics, analytics,
> optimiser and strategies here are written from scratch* (`pqlab/`,
> `strategies/`). To prove the engine is correct I diff-test it against an
> existing open-source reference simulator
> ([`chrispyroberts/imc-prosperity-4`](https://github.com/chrispyroberts/imc-prosperity-4),
> which wraps the well-known `prosperity3bt`); that reference is **not** my code
> and is used only as an oracle. See *Validation* below.

---

## Why this is more than a toy

- **It matches like the real venue.** Orders sweep the visible book, then rest
  against the same tick's market trades — the channel through which passive
  maker quotes actually fill. Position-limit breaches reject a product's whole
  order set, exactly as Prosperity does.
- **It's provably correct.** `scripts/validate_against_reference.py` runs
  **5,000 randomised order/book/trade scenarios** through both my matcher and
  the reference engine and asserts **identical fills, position and cash**. It
  passes.
- **It refuses to flatter you.** Metrics are reported as a bundle (PnL,
  Sharpe, Sortino, max drawdown, turnover, fill efficiency), and the optimiser
  is **walk-forward**: parameters are chosen on one day and scored on a later,
  unseen day, so the headline number is genuinely out-of-sample.
- **It thinks like a market-making desk.** Mark-out (adverse-selection)
  analysis measures where the mid goes right *after* each fill — the difference
  between "made money" and "got picked off but the trend bailed me out".

## Layout

```
pqlab/
  datamodel.py     Prosperity-compatible Order / OrderDepth / TradingState
  data.py          loader for the real prices_*.csv / trades_*.csv
  backtester.py    the from-scratch event-driven matching engine
  metrics.py       PnL / Sharpe / Sortino / drawdown / turnover / utilisation
  optimize.py      grid search + walk-forward (in-sample vs out-of-sample)
  montecarlo.py    stationary block-bootstrap robustness (PnL distribution, VaR)
  analytics/
    markout.py     mark-out / adverse-selection profiles
strategies/
  flagship_hybrid.py   my Round-2 submission: Kalman + Bayesian + OFI maker
  baselines.py         fixed-spread MM, inventory-skew MM, mean-reversion taker
scripts/
  backtest.py                    run one strategy on one day, print metrics
  run_research.py                full study -> results/REPORT.md + plots
  validate_against_reference.py  differential test vs the reference engine
tests/
  test_engine.py   hand-computed matching cases + invariants on real data
data/
  round1/ round2/  real captured order books (3 days each)
```

## Quick start

```bash
pip install -r requirements.txt

# one strategy, one day
python scripts/backtest.py flagship_hybrid --round 2 --day 0

# the full study: strategy comparison, mark-out, walk-forward, plots, report
python scripts/run_research.py            # writes results/REPORT.md + PNGs

# Monte Carlo robustness: is the edge real or one lucky path?
python scripts/run_montecarlo.py flagship_hybrid --day 0 --paths 800

# prove the engine is correct against the reference simulator
python scripts/validate_against_reference.py

# tests
python tests/test_engine.py               # (or: pytest tests/)
```

## Strategies

- **`flagship_hybrid`** — my actual Round-2 submission, ported verbatim (only
  the import path changed). Osmium is priced with a fair value that fuses a
  Kalman filter and a Bayesian precision-weighted mid, nudged by order-flow
  imbalance and an inventory-risk term, then quoted with an elastic peg and
  volume laddering. Pepper is a buy-and-hold to the limit.
- **`inventory_skew_mm`** — a clean, parameterised maker that skews its quotes
  against inventory; this is the one the walk-forward optimiser tunes.
- **`fixed_spread_mm`**, **`mean_reversion_taker`** — simple baselines so the
  flagship is measured against something, not graded in a vacuum.

## Headline findings

Full, auto-generated numbers live in **`results/REPORT.md`**. The honest
summary (mean across the 3 Round-2 days):

- The flagship earns **~95.9k seashells/day**, Sharpe/day ~3.0, max drawdown
  ~2k. The naive baselines all **lose money** (fixed-spread −62k,
  inventory-skew −23k, mean-reversion −137k) — realistic: undefended market
  making gets adversely selected. Measuring the flagship against losers that
  *look* reasonable is the point.
- **Mark-out separates skill from beta.** The flagship's Osmium fills carry a
  healthy **+2.4 seashells/unit** mark-out at every horizon (genuine maker
  edge). Its Pepper fills have a **negative** mark-out (~−7): they lose money
  right after execution, so Pepper's PnL is pure trend beta that the
  buy-and-hold happens to ride — not fill quality.
- **Walk-forward exposes overfitting.** Tuning `inventory_skew_mm` on day −1
  gives +2.6k in-sample but **−24.6k out-of-sample** on day 0. Picking the best
  in-sample number would have wildly overstated it — exactly the trap a
  backtester exists to catch.
- **Monte Carlo says the flagship's edge is robust.** Block-bootstrapping day 0
  into 800 alternate sessions: **100% are profitable**, mean ~93.9k ± 5.2k, and
  the daily-Sharpe 90% confidence interval is **[2.60, 3.16] — it excludes
  zero**. So the result isn't one lucky path.

## Method notes & honesty caveats

- **PnL** is marked to mid each tick; ticks with no quotes (the raw data has
  `mid_price = 0` rows) carry the last valid mid forward instead of marking a
  held position at zero.
- **Sharpe** is computed on per-tick PnL *increments* and scaled to a daily
  figure. At this frequency a lone Sharpe number is close to meaningless, so it
  is never reported on its own.
- This backtester assumes your orders don't move the market and that historical
  market trades would still have printed against your quotes — the usual
  replay-backtest assumptions. It is a research tool, not a fill simulator for
  live capital.
