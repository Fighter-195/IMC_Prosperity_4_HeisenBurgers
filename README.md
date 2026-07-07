# IMC Prosperity 4 — Team HEISENBURGERS

Algorithmic + manual trading strategies for **IMC Prosperity 4**, a global
quantitative trading competition, together with a **from-scratch limit-order-book
backtester** built afterwards to validate the algorithms offline.

## 🏆 Final results(Round 2)

Out of **18,000+ teams** worldwide:

| Category | Global rank |
|---|---:|
| **Manual trading** | **#6** |
| Overall | #1016 |
| Algorithmic | #3178 |
| Country | #227 |

**Team total: 448,174 seashells** (cumulative across **rounds 1 + 2**), against a
required threshold of **200,000** — **~2.24× the target**.

The standout **#6 global Manual** placement came from the capital-allocation
optimization detailed in [`manual/`](manual/README.md).

---

## The competition

Prosperity gives each team a live simulated exchange but only a **black-box online
evaluator** — you submit a `Trader` class, it runs against hidden order-book data,
and you get back a PnL. Each round adds products and mechanics. This repo covers
the **algorithmic trading** track (rounds 1–2) plus a research harness that
rebuilds the part the competition hides: an offline backtester you fully control.

**Products traded (rounds 1–2), position limit ±80 each:**

| Product | Behaviour | Our treatment |
|---|---|---|
| `ASH_COATED_OSMIUM` | mean-reverting, anchored ~10,000 | the real market-making edge |
| `INTARIAN_PEPPER_ROOT` | trending, ~12,000 | directional buy-and-hold (beta) |

Every tick the algorithm receives the current `OrderDepth` per product and returns
orders plus a JSON `traderData` string that is the **only** state that survives to
the next tick — so all filter/model state is serialised each step.

---

## Approach — algorithm design per round

### Round 1 — online AR(2) + mean-reversion + adaptive maker
`submissions/round1/round1_final.py`

Fair value is a **volume-weighted micro-price**
`mid = (best_bid·ask_vol + best_ask·bid_vol) / (bid_vol + ask_vol)` — each side
weighted by the *opposite* side's size, which leans the estimate toward where the
book will actually clear.

On top of that, Osmium runs a layered signal:

1. **Online AR(2) predictor.** Models price *increments* `xₜ = pₜ − pₜ₋₁` as
   `x̂ = φ₁·x₁ + φ₂·x₂` and learns `φ₁, φ₂` live with a **normalised-LMS** update
   (`φ += LR·err·x / (‖x‖² + ε)`). The update is **volatility-gated** (only when
   `‖x‖² > 1`) so it doesn't learn from quantisation noise, and the coefficients
   are clamped to `[−1, 1]` for stability.
2. **Mean-reversion term** over a 40-tick window, `(long_mean − mid)·strength`.
3. **Regime blend.** When the AR signal is strong (`|pred| > 1.5`) it weights
   momentum 0.8 / reversion 0.2; otherwise 0.4 / 0.6.
4. **Adaptive market-making failsafe.** When the combined signal flatlines
   (`|signal| < 0.4`) it falls back to a pure maker whose fair value uses
   **order-book imbalance** and whose quoting edge **scales with the spread**
   (`maker_edge = spread·0.25`) — quote wide when the book is wide.
5. **Taker then maker.** Sweep the book when price crosses fair ± edge, then rest
   bracketing quotes with a strict 1-tick anti-cross guard.

Pepper is handled as a **staggered accumulate-and-hold** directional bet: buy-only,
small initial bite then larger staggered adds, never sells (holds to settlement).

### Round 2 — Kalman + Bayesian + Order-Flow-Imbalance hybrid
`submissions/round2/round2_final.py`

A more principled market-maker on Osmium, pegged to the fair value FV = 10,000:

1. **Kalman filter** (`Q = 1e-5`, `R = 0.2`) tracks the latent fair value `x` and
   its uncertainty `P`. Low process noise → a "stubborn" filter that trusts its own
   estimate over noisy mids.
2. **Bayesian precision-weighted fusion.** Combines the raw mid and the Kalman
   estimate weighted by their **inverse variances**
   (`w = precision / total_precision`), so the estimate automatically leans on
   whichever source is currently more confident (variance from a 20-tick window,
   tuned for the best predictive correlation).
3. **Order-Flow Imbalance (OFI).** Cont-style level+size change tracking
   (`e_b − e_a`) to read directional pressure from the book, folded into the signal.
4. **Cubic inventory-risk skew.** Fair value is pushed against the current
   position with a cubic urgency multiplier `1 + urgency³·4`, so the risk penalty
   ramps hard only as inventory approaches the ±80 limit.
5. **Dynamic taker edge** — tightens to 0.5 on high conviction, widens to 1.5 on
   noise.
6. **Elastic 10k peg + volume laddering.** Quotes anchor around FV but "stretch"
   the peg proportional to inventory (`|pos|/20`) to escape one-sided lock-up;
   orders ladder in two tranches (40 at best, remainder two ticks back).

The Round-2 sealed market-access **fee bid** is submitted via `bid()`.

---

## The extension — a from-scratch LOB backtester
`backtester/`  ·  see `backtester/README.md` for full detail

Because Prosperity only exposes a black-box evaluator, this is a **market-making
research harness owned end to end** (~900 LOC), built to measure the strategies
above honestly and offline.

- **Event-driven limit-order-book matching engine** — per tick: a position-limit
  gate (a product's whole order set is rejected on breach, exactly like
  Prosperity), a **book channel** (sweep visible asks/bids, price priority), and a
  **market-trade channel** (resting maker quotes fill against that tick's printed
  trades — where inside-spread maker fills actually come from). Mark-to-mid PnL.
- **Provably correct** — `scripts/validate_against_reference.py` diff-tests the
  matcher against an open-source reference simulator over **5,000 randomised
  order/book/trade scenarios** and asserts identical fills, position and cash. It
  passes; unit tests cover the engine.
- **Honest metrics** — PnL, Sharpe, Sortino, max drawdown, turnover, fill
  efficiency as a bundle; a **walk-forward optimiser** tunes on one day and scores
  on a later, unseen day, so headline numbers are genuinely out-of-sample.
- **Desk-grade analytics** — **mark-out / adverse-selection** analysis measures
  where the mid goes right after each fill, separating real maker edge from
  "got picked off but the trend bailed me out."
- **Monte-Carlo** — stationary block-bootstrap of real PnL increments into a
  distribution with confidence intervals.

Our real Round-2 strategy is ported verbatim as
`backtester/strategies/flagship_hybrid.py` and measured against naive baselines.

### What the backtester revealed (honest findings)

- Flagship market-maker ≈ **95.9k PnL/day at Sharpe ~3**; Monte-Carlo put the
  daily-Sharpe 90% CI at **[2.60, 3.16]** (excludes zero), 100% of bootstrapped
  day-0 sessions profitable.
- **Osmium is the true maker edge** (mark-out **+2.4**); **Pepper's PnL is trend
  beta, not skill** (mark-out ≈ **−7** — adversely selected, saved by the trend).
- Walk-forward **exposed overfitting** on naive configs (in-sample +2.6k →
  out-of-sample −24.6k) — the kind of result a black-box evaluator hides.

---

## Repository structure

```
IMC_Prosperity_4_HeisenBurgers/
├── manual/                       # #6-global manual round: capital-allocation optimization writeup
├── submissions/                  # the two final, promoted algorithms
│   ├── datamodel.py              # IMC-provided trading data model (for reference/runnability)
│   ├── round1/round1_final.py    # AR(2) + mean-reversion + adaptive maker
│   └── round2/round2_final.py    # Kalman + Bayesian + OFI hybrid
├── iterations/                   # raw dev trail (filenames = backtest PnL) — the grind
│   ├── round1/                   # 38-file Osmium/Pepper research trail + param sweeps
│   └── round2/                   # Round-2 exploration + price-analysis notebook
└── backtester/                   # from-scratch LOB backtester + research framework
    ├── README.md
    ├── pqlab/                    # engine, metrics, optimiser, montecarlo, analytics
    ├── strategies/              # flagship_hybrid (our Round-2 algo) + baselines
    ├── scripts/                 # backtest / research / montecarlo / validation runners
    ├── tests/                   # engine unit tests
    ├── data/round1, data/round2 # the competition order-book datasets
    └── requirements.txt
```

## Running it

```bash
cd backtester
pip install -r requirements.txt

python scripts/backtest.py                  # backtest a strategy on a day
python scripts/validate_against_reference.py # 5,000-scenario engine diff-test
python scripts/run_research.py              # metrics + mark-out analysis
python scripts/run_montecarlo.py            # bootstrap PnL distribution
python -m pytest tests/                      # engine unit tests
```

The `submissions/` files are the exact competition algorithms; on the Prosperity
platform they run against the provided `datamodel.py` (included here for
reference), and can be evaluated locally through the backtester.

---

<sub>IMC Prosperity 4 · Team HEISENBURGERS · #6 global manual, #1016 overall of 18,000+ teams · 448,174 seashells across rounds 1+2 (2.24× the 200k threshold).</sub>
