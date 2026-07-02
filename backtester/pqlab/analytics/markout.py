"""Mark-out (adverse-selection) analysis.

When a market maker gets filled, the honest question is: where did the price go
*right after*?  If your buys are systematically followed by the mid dropping,
you are being adversely selected -- picked off by better-informed flow -- even
if end-of-day PnL looks fine.  Mark-out PnL at horizon ``h`` is, per fill:

    buy  fill:  mid(t+h) - fill_price
    sell fill:  fill_price - mid(t+h)

Averaged over fills it is the per-unit edge (in seashells) the desk captures at
each horizon.  Rising-then-flat is healthy; negative means toxic flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from pqlab.backtester import BacktestResult


@dataclass
class MarkoutResult:
    horizons: List[int]
    # product -> {horizon -> mean markout per unit}
    per_product: Dict[str, Dict[int, float]] = field(default_factory=dict)
    # aggregate across all products, volume weighted
    overall: Dict[int, float] = field(default_factory=dict)
    n_fills: int = 0

    def summary_lines(self) -> List[str]:
        lines = [f"Mark-out (per-unit seashells), {self.n_fills} fills"]
        lines.append("  horizon(ticks): " + "  ".join(f"{h:>6}" for h in self.horizons))
        lines.append("  overall edge  : " + "  ".join(f"{self.overall[h]:>6.2f}" for h in self.horizons))
        for p, hv in self.per_product.items():
            lines.append(f"  {p[:18]:<18}: " + "  ".join(f"{hv[h]:>6.2f}" for h in self.horizons))
        return lines


def markout_analysis(result: BacktestResult, horizons=(1, 5, 10, 50)) -> MarkoutResult:
    horizons = list(horizons)
    # index timestamp -> position in the aligned series
    ts_index = {ts: i for i, ts in enumerate(result.timestamps)}
    mids = {p: np.asarray(s, dtype=float) for p, s in result.mid_series.items()}

    # accumulate sums per product/horizon
    sums: Dict[str, Dict[int, float]] = {p: {h: 0.0 for h in horizons} for p in result.products}
    counts: Dict[str, Dict[int, int]] = {p: {h: 0 for h in horizons} for p in result.products}

    for tr in result.own_trades:
        p = tr.symbol
        if p not in mids:
            continue
        i = ts_index.get(tr.timestamp)
        if i is None:
            continue
        sign = 1.0 if tr.buyer == "SUBMISSION" else -1.0  # +1 we bought, -1 we sold
        qty = abs(tr.quantity)
        series = mids[p]
        for h in horizons:
            j = i + h
            if j >= len(series):
                continue
            future_mid = series[j]
            if future_mid <= 0:
                continue
            mo = sign * (future_mid - tr.price)
            sums[p][h] += mo * qty
            counts[p][h] += qty

    per_product: Dict[str, Dict[int, float]] = {}
    overall_sum = {h: 0.0 for h in horizons}
    overall_cnt = {h: 0 for h in horizons}
    for p in result.products:
        per_product[p] = {}
        for h in horizons:
            c = counts[p][h]
            per_product[p][h] = (sums[p][h] / c) if c else 0.0
            overall_sum[h] += sums[p][h]
            overall_cnt[h] += c

    overall = {h: (overall_sum[h] / overall_cnt[h]) if overall_cnt[h] else 0.0 for h in horizons}

    return MarkoutResult(
        horizons=horizons,
        per_product=per_product,
        overall=overall,
        n_fills=len(result.own_trades),
    )
