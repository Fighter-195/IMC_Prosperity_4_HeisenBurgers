"""Small CLI: run one strategy on one (round, day) and print its metrics.

    python scripts/backtest.py flagship_hybrid --round 2 --day 0
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pqlab import Backtester, load_day, compute_metrics
from pqlab.data import LIMITS
from pqlab.metrics import per_product_pnl, position_utilization
from strategies import make, REGISTRY


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", choices=list(REGISTRY))
    ap.add_argument("--round", type=int, default=2)
    ap.add_argument("--day", type=int, default=0)
    ap.add_argument("--match", default="all", choices=["all", "worse", "none"])
    args = ap.parse_args()

    day = load_day(args.round, args.day)
    res = Backtester(match_mode=args.match).run(make(args.strategy), day)
    m = compute_metrics(res)

    print(f"\n{args.strategy}  round {args.round} day {args.day}  ({len(day.timestamps)} ticks)")
    print("-" * 60)
    for k, v in m.as_dict().items():
        print(f"  {k:<22} {v:>14.3f}" if isinstance(v, float) else f"  {k:<22} {v:>14}")
    print("\n  PnL attribution:")
    for p, v in per_product_pnl(res).items():
        print(f"    {p:<22} {v:>12.1f}")
    print("\n  Position utilisation (mean |pos|/limit):")
    for p, v in position_utilization(res, LIMITS).items():
        print(f"    {p:<22} {v:>12.2%}")
    print()


if __name__ == "__main__":
    main()
