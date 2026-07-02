"""Monte Carlo robustness test for a strategy, with a fan chart + PnL histogram.

    python scripts/run_montecarlo.py flagship_hybrid --day 0 --paths 800

Bootstraps the strategy's real per-tick PnL into many alternate sessions and
reports the distribution of outcomes (see pqlab/montecarlo.py for the method).
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pqlab import Backtester, load_day
from pqlab.montecarlo import monte_carlo_backtest
from strategies import make, REGISTRY

RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
os.makedirs(RESULTS, exist_ok=True)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_PLT = True
except Exception:
    HAVE_PLT = False


def fan_chart(res, mc, name, day):
    if not HAVE_PLT:
        return None
    ts = mc.timestamps
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.5), gridspec_kw={"width_ratios": [2, 1]})

    ax[0].fill_between(ts, mc.bands[5], mc.bands[95], color="steelblue", alpha=0.25, label="5-95%")
    ax[0].fill_between(ts, mc.bands[25], mc.bands[75], color="steelblue", alpha=0.45, label="25-75%")
    ax[0].plot(ts, mc.bands[50], color="navy", lw=1.2, label="median path")
    ax[0].plot(res.timestamps, res.total_pnl, color="crimson", lw=1.3, label="observed backtest")
    ax[0].set_title(f"{name} -- Monte Carlo fan chart (day {day}, {mc.n_paths} paths)")
    ax[0].set_xlabel("timestamp"); ax[0].set_ylabel("PnL (seashells)")
    ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3)

    ax[1].hist(mc.finals, bins=40, color="steelblue", alpha=0.8)
    ax[1].axvline(0, color="k", lw=0.8)
    ax[1].axvline(mc.p05, color="crimson", lw=1.4, ls="--", label=f"5% worst = {mc.p05:.0f}")
    ax[1].axvline(mc.observed_final, color="green", lw=1.4, label="observed")
    ax[1].set_title("End-of-day PnL distribution")
    ax[1].set_xlabel("PnL"); ax[1].legend(fontsize=8); ax[1].grid(alpha=0.3)

    plt.tight_layout()
    p = os.path.join(RESULTS, f"montecarlo_{name}_day{day}.png")
    plt.savefig(p, dpi=110); plt.close()
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", nargs="?", default="flagship_hybrid", choices=list(REGISTRY))
    ap.add_argument("--round", type=int, default=2)
    ap.add_argument("--day", type=int, default=0)
    ap.add_argument("--paths", type=int, default=800)
    ap.add_argument("--block", type=int, default=50, help="mean bootstrap block length (ticks)")
    args = ap.parse_args()

    day = load_day(args.round, args.day)
    res = Backtester().run(make(args.strategy), day)
    mc = monte_carlo_backtest(res, n_paths=args.paths, mean_block=args.block)

    print(f"\n{args.strategy}  round {args.round} day {args.day}")
    print("-" * 64)
    print("\n".join(mc.summary_lines()))

    edge = "looks robust" if mc.prob_loss < 0.1 and mc.sharpe_lo > 0 else "is fragile / not clearly positive"
    print(f"\n  Read: the strategy's edge {edge} -- "
          f"{1 - mc.prob_loss:.0%} of bootstrapped sessions are profitable and the "
          f"daily-Sharpe confidence interval {'excludes' if mc.sharpe_lo > 0 else 'includes'} zero.")

    p = fan_chart(res, mc, args.strategy, args.day)
    if p:
        print("\n  fan chart ->", os.path.basename(p))
    else:
        print("\n  [matplotlib not installed -- skipped fan chart]")
    print()


if __name__ == "__main__":
    main()
