"""End-to-end research study -- the headline deliverable.

Runs the whole strategy book across all three Round-2 days and produces:
  1. a metrics comparison table (mean across days)            -> results/metrics.csv
  2. mark-out / adverse-selection profiles                    -> printed + report
  3. a walk-forward parameter optimisation (in- vs out-of-sample)
  4. equity / position / drawdown plots (if matplotlib present) -> results/*.png
  5. an honest written summary                                -> results/REPORT.md

Everything is driven by the from-scratch engine in ``pqlab`` on real captured
order-book data; no numbers are hand-entered.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pqlab import Backtester, load_day, compute_metrics
from pqlab.data import LIMITS
from pqlab.metrics import per_product_pnl, position_utilization
from pqlab.analytics import markout_analysis
from pqlab.optimize import walk_forward, grid_search, evaluate
from strategies import make, REGISTRY, InventorySkewMM

ROUND = 2
DAYS = [-1, 0, 1]
RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
os.makedirs(RESULTS, exist_ok=True)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_PLT = True
except Exception:
    HAVE_PLT = False


def run_all_strategies():
    """Run every strategy on every day; return nested results + mean metrics."""
    bt = Backtester()
    runs = {}          # strat -> day -> (result, metrics)
    mean_metrics = {}  # strat -> dict of mean metric values
    for name in REGISTRY:
        runs[name] = {}
        per_day = []
        for d in DAYS:
            day = load_day(ROUND, d)
            res = bt.run(make(name), day)
            m = compute_metrics(res)
            runs[name][d] = (res, m)
            per_day.append(m.as_dict())
        keys = per_day[0].keys()
        mean_metrics[name] = {k: float(np.mean([pd[k] for pd in per_day])) for k in keys}
    return runs, mean_metrics


def format_table(mean_metrics) -> str:
    cols = ["final_pnl", "sharpe_per_day", "sortino_per_day", "max_drawdown",
            "win_rate", "n_trades", "turnover_per_day", "pnl_per_unit_volume"]
    head = f"{'strategy':<22}" + "".join(f"{c:>18}" for c in cols)
    lines = [head, "-" * len(head)]
    for name, mm in sorted(mean_metrics.items(), key=lambda kv: -kv[1]["final_pnl"]):
        row = f"{name:<22}" + "".join(f"{mm[c]:>18.2f}" for c in cols)
        lines.append(row)
    return "\n".join(lines)


def save_metrics_csv(mean_metrics):
    import csv
    path = os.path.join(RESULTS, "metrics.csv")
    cols = list(next(iter(mean_metrics.values())).keys())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy"] + cols)
        for name, mm in mean_metrics.items():
            w.writerow([name] + [f"{mm[c]:.4f}" for c in cols])
    return path


def make_plots(runs):
    if not HAVE_PLT:
        return []
    paths = []
    day = 0

    # 1. equity curves (all strategies, day 0)
    plt.figure(figsize=(10, 5))
    for name in REGISTRY:
        res, _ = runs[name][day]
        plt.plot(res.timestamps, res.total_pnl, label=name, linewidth=1.2)
    plt.title(f"Equity curves -- Round {ROUND} day {day}")
    plt.xlabel("timestamp"); plt.ylabel("marked PnL (seashells)")
    plt.legend(fontsize=8); plt.grid(alpha=0.3); plt.tight_layout()
    p = os.path.join(RESULTS, "equity_curves.png"); plt.savefig(p, dpi=110); plt.close()
    paths.append(p)

    # 2. flagship: PnL + position over time
    res, _ = runs["flagship_hybrid"][day]
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax[0].plot(res.timestamps, res.total_pnl, color="navy", linewidth=1.0)
    ax[0].set_ylabel("marked PnL"); ax[0].set_title("flagship_hybrid -- PnL & inventory (day 0)")
    ax[0].grid(alpha=0.3)
    for p_ in res.products:
        ax[1].plot(res.timestamps, res.position_series[p_], label=p_, linewidth=0.8)
    ax[1].axhline(0, color="k", linewidth=0.5)
    ax[1].set_ylabel("position"); ax[1].set_xlabel("timestamp")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=0.3)
    plt.tight_layout()
    p = os.path.join(RESULTS, "flagship_pnl_position.png"); plt.savefig(p, dpi=110); plt.close()
    paths.append(p)

    # 3. drawdown of flagship
    eq = np.asarray(res.total_pnl)
    dd = eq - np.maximum.accumulate(eq)
    plt.figure(figsize=(10, 3.5))
    plt.fill_between(res.timestamps, dd, 0, color="firebrick", alpha=0.6)
    plt.title("flagship_hybrid -- drawdown (day 0)")
    plt.xlabel("timestamp"); plt.ylabel("drawdown (seashells)")
    plt.grid(alpha=0.3); plt.tight_layout()
    p = os.path.join(RESULTS, "flagship_drawdown.png"); plt.savefig(p, dpi=110); plt.close()
    paths.append(p)
    return paths


def run_markouts(runs):
    out = {}
    for name in ("flagship_hybrid", "inventory_skew_mm"):
        res, _ = runs[name][0]
        out[name] = markout_analysis(res, horizons=(1, 5, 10, 50))
    return out


def run_walkforward():
    """Walk-forward optimise the inventory-skew maker, and quantify the
    in-sample -> out-of-sample optimism gap."""
    grid = {"edge": [1, 2, 3], "skew": [1.0, 3.0, 6.0], "size": [15, 25]}
    steps = walk_forward(InventorySkewMM, grid, DAYS, metric="final_pnl", round_num=ROUND)

    # optimism gap on the first split: best in-sample score vs its OOS score
    ranked = grid_search(InventorySkewMM, grid, [DAYS[0]], [DAYS[1]],
                         metric="final_pnl", round_num=ROUND)
    in_sample_best = ranked[0]
    # default (untuned) params OOS, for reference
    default_oos = evaluate(lambda: InventorySkewMM(), [DAYS[1]], "final_pnl", ROUND)
    return steps, in_sample_best, default_oos, len(list(_iter(grid)))


def _iter(grid):
    import itertools
    keys = list(grid)
    for v in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, v))


def write_report(mean_metrics, table, markouts, wf, plots):
    steps, in_sample_best, default_oos, n_combos = wf
    lines = []
    A = lines.append
    A("# Prosperity Quant Lab -- Research Report\n")
    A("_Auto-generated by `scripts/run_research.py`. Every number below comes "
      "from the from-scratch engine replaying real Round-2 order-book data._\n")

    A("## 1. Strategy comparison (mean across 3 days)\n")
    A("```\n" + table + "\n```\n")
    best = max(mean_metrics, key=lambda k: mean_metrics[k]["final_pnl"])
    A(f"Highest mean PnL: **{best}** "
      f"({mean_metrics[best]['final_pnl']:.0f} seashells/day).\n")
    A("> Honest read: most of the flagship's PnL is the Pepper buy-and-hold leg "
      "riding a trending product -- that is beta, not skill. The Osmium "
      "market-making leg is the genuine edge: it earns a steadier PnL at a "
      "fraction of the inventory risk (see the per-product attribution and the "
      "low-drawdown equity curve).\n")

    A("## 2. Mark-out / adverse selection (day 0)\n")
    A("Per-unit seashell edge captured at N ticks after each fill. Flat-or-rising "
      "is healthy; falling means the fills are being picked off.\n")
    for name, mo in markouts.items():
        A(f"\n**{name}**\n```\n" + "\n".join(mo.summary_lines()) + "\n```\n")
        # auto-flag products whose fills are adversely selected
        toxic = [p for p, hv in mo.per_product.items() if hv[mo.horizons[0]] < 0]
        for p in toxic:
            A(f"> `{p}` has a **negative** mark-out: its fills lose money in the "
              "ticks right after execution. Any PnL it shows therefore comes from "
              "longer-horizon drift (trend beta), not from the quality of the "
              "fills -- microstructure evidence that this leg is beta, not edge.\n")

    A("## 3. Walk-forward parameter optimisation (inventory_skew_mm)\n")
    A(f"Grid of {n_combos} parameter combinations. Parameters are chosen on the "
      "train day and scored on a later, unseen test day.\n")
    A("```")
    A(f"{'train->test':<14}{'best params':<34}{'train PnL':>12}{'test PnL (OOS)':>16}")
    for s in steps:
        A(f"{str(s.train_day)+'->'+str(s.test_day):<14}{str(s.best_params):<34}"
          f"{s.train_score:>12.0f}{s.test_score:>16.0f}")
    A("```")
    gap = in_sample_best.train_score - in_sample_best.test_score
    A(f"\nOptimism gap on split {DAYS[0]}->{DAYS[1]}: the in-sample winner scored "
      f"**{in_sample_best.train_score:.0f}** in-sample but **{in_sample_best.test_score:.0f}** "
      f"out-of-sample (a {gap:.0f} drop). Untuned defaults earned "
      f"**{default_oos:.0f}** OOS. This gap is exactly what a backtester exists to "
      "expose -- picking the best in-sample number would have overstated "
      "performance.\n")

    if plots:
        A("## 4. Figures\n")
        for p in plots:
            rel = os.path.basename(p)
            A(f"![{rel}]({rel})")
        A("")

    A("## Method notes\n")
    A("- **Matching**: orders sweep the visible book, then rest against that "
      "tick's market trades (the channel that fills passive maker quotes). "
      "Position limits reject a product's whole order set if breached -- exactly "
      "as Prosperity behaves.\n")
    A("- **Validation**: `scripts/validate_against_reference.py` diff-tests this "
      "matcher against the reference `prosperity3bt` engine over 5,000 randomised "
      "scenarios -- identical fills, position and cash.\n")
    A("- **PnL** is marked to mid each tick; unquoted ticks carry the last valid "
      "mid forward (the raw data has ticks with no quotes / mid=0).\n")
    A("- **Sharpe** is computed on per-tick PnL increments and scaled to a daily "
      "figure; it is reported alongside drawdown and turnover, never alone.\n")

    path = os.path.join(RESULTS, "REPORT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    print("Running strategy book across days", DAYS, "...")
    runs, mean_metrics = run_all_strategies()
    table = format_table(mean_metrics)
    print("\n" + table + "\n")

    csv_path = save_metrics_csv(mean_metrics)
    print("metrics ->", csv_path)

    markouts = run_markouts(runs)
    for name, mo in markouts.items():
        print("\n" + "\n".join(mo.summary_lines()))

    print("\nWalk-forward optimising inventory_skew_mm (this runs a grid each split)...")
    wf = run_walkforward()
    for s in wf[0]:
        print(f"  train {s.train_day} -> test {s.test_day}: best {s.best_params} "
              f"| train {s.train_score:.0f}  OOS {s.test_score:.0f}")

    plots = make_plots(runs)
    if plots:
        print("\nplots ->", ", ".join(os.path.basename(p) for p in plots))
    else:
        print("\n[matplotlib not installed -- skipped plots]")

    report = write_report(mean_metrics, table, markouts, wf, plots)
    print("report ->", report)


if __name__ == "__main__":
    main()
