"""Performance and risk metrics computed from a backtest's PnL path.

A deliberate note on Sharpe at this frequency
---------------------------------------------
Prosperity PnL is an *absolute* number in seashells, not a percentage return
on capital, so everything here is computed on the per-tick PnL *increment*
series ``d = diff(pnl)``.  The raw ``mean(d)/std(d)`` is a per-tick Sharpe and
is frequency dependent -- quoting it alone is meaningless.  We therefore report
it scaled to a per-day figure via ``sqrt(ticks_per_day)``, and always alongside
total PnL, drawdown and turnover so no single number can flatter a strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List

import numpy as np

from pqlab.backtester import BacktestResult


@dataclass
class Metrics:
    final_pnl: float
    mean_tick_pnl: float
    vol_tick_pnl: float
    sharpe_per_tick: float
    sharpe_per_day: float
    sortino_per_day: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    n_trades: int
    volume_traded: int
    turnover_per_day: float
    pnl_per_unit_volume: float
    limit_breaches: int

    def as_dict(self) -> Dict:
        return asdict(self)


def _max_drawdown(equity: np.ndarray) -> "tuple[float, float]":
    """Largest peak-to-trough drop of the (absolute) equity curve.

    Returns (absolute_drawdown, pct_of_peak).  Percentage uses the running
    peak as the base where that peak is positive, else falls back to absolute.
    """
    running_peak = np.maximum.accumulate(equity)
    drawdowns = equity - running_peak
    i = int(np.argmin(drawdowns))
    abs_dd = float(-drawdowns[i])
    peak = running_peak[i]
    pct = float(abs_dd / peak) if peak > 1e-9 else float("nan")
    return abs_dd, pct


def compute_metrics(result: BacktestResult, ticks_per_day: int = None) -> Metrics:
    equity = np.asarray(result.total_pnl, dtype=float)
    if equity.size == 0:
        raise ValueError("empty PnL series")

    d = np.diff(equity)
    if d.size == 0:
        d = np.zeros(1)

    if ticks_per_day is None:
        ticks_per_day = len(equity)

    mean_tick = float(np.mean(d))
    vol_tick = float(np.std(d, ddof=1)) if d.size > 1 else 0.0
    sharpe_tick = mean_tick / vol_tick if vol_tick > 1e-12 else 0.0
    sharpe_day = sharpe_tick * np.sqrt(ticks_per_day)

    downside = d[d < 0]
    downside_vol = float(np.std(downside, ddof=1)) if downside.size > 1 else 0.0
    sortino_day = (mean_tick / downside_vol * np.sqrt(ticks_per_day)) if downside_vol > 1e-12 else 0.0

    abs_dd, pct_dd = _max_drawdown(equity)

    nonzero = d[d != 0]
    win_rate = float(np.mean(nonzero > 0)) if nonzero.size else 0.0

    n_trades = len(result.own_trades)
    volume = int(sum(abs(t.quantity) for t in result.own_trades))
    n_days = max(1, len(equity) / ticks_per_day)
    turnover_per_day = volume / n_days
    pnl_per_unit_vol = float(equity[-1] / volume) if volume > 0 else 0.0

    return Metrics(
        final_pnl=float(equity[-1]),
        mean_tick_pnl=mean_tick,
        vol_tick_pnl=vol_tick,
        sharpe_per_tick=sharpe_tick,
        sharpe_per_day=float(sharpe_day),
        sortino_per_day=float(sortino_day),
        max_drawdown=abs_dd,
        max_drawdown_pct=pct_dd,
        win_rate=win_rate,
        n_trades=n_trades,
        volume_traded=volume,
        turnover_per_day=turnover_per_day,
        pnl_per_unit_volume=pnl_per_unit_vol,
        limit_breaches=result.n_limit_breaches,
    )


def per_product_pnl(result: BacktestResult) -> Dict[str, float]:
    """Final marked PnL attributed to each product."""
    return {p: (series[-1] if series else 0.0) for p, series in result.pnl_series.items()}


def position_utilization(result: BacktestResult, limits: Dict[str, int]) -> Dict[str, float]:
    """Mean |position| / limit per product -- how hard the book is being used."""
    out = {}
    for p, series in result.position_series.items():
        if not series:
            out[p] = 0.0
            continue
        lim = limits.get(p, 1)
        out[p] = float(np.mean(np.abs(series)) / lim) if lim else 0.0
    return out
