# Development trail — iterations

The raw, unpolished development history behind the two final submissions. Kept
here so the actual work is visible: many of the filenames are the **backtest PnL
that version scored** (e.g. `2650.py`, `59890.py`, `103520.py`), so you can read
the progression as a scoreboard. Not cleaned up on purpose — this is the grind.

The curated, final algorithms live in [`../submissions/`](../submissions); the
offline research harness lives in [`../backtester/`](../backtester).

## `round1/` — 38 files
The full Osmium/Pepper research trail. Roughly in order of evolution:
- **Early ideas / mean-reversion:** `algo.py`, `mean_rev_t.py`, `trader.py`,
  `1900.py`, `2650.py`, `fusion.py`, `test_1.py`, `test_2.py`, `testing.py`.
- **Osmium market-maker line:** `osmium.py`, `osmium_2.py`, `osmium_test.py`,
  `osmium_best.py`, `sim.py`, `sim_osmium.py`.
- **PnL-tagged checkpoints:** `9900.py`, `9940.py`, `10k.py`, `59890.py`,
  `66149.py`, `103520.py` (the number = that build's backtest PnL).
- **Parameter sweeps (tuning effort):** `sweep_progress.json`,
  `sweep_results.json`, `multi_sweep_progress.json`, `osmium_sweep_results.json`,
  `skew_sweep_progress.json`.
- **Converged Round-1 algo:** `round1.py` → `round1_best.py` → `round1_goat.py`
  (the last is what was promoted to `submissions/round1/round1_final.py`).
- Misc: `setup.py`, `submission.py`, `demo_sub.py`, `initial_csv_visual.py`,
  `abhinav.py`.

## `round2/` — 7 files
The Round-2 exploration around the Kalman + Bayesian + OFI hybrid:
- `round2_first.py` — the earlier Round-2 variant before the final tune.
- `480234.py`, `besttttt.py`, `hit_or_miss.py`, `sub.py`, `demo.py` —
  exploratory maker/taker experiments.
- `price_graphs.ipynb` — price/microstructure analysis notebook.

The promoted final is `submissions/round2/round2_final.py`.
