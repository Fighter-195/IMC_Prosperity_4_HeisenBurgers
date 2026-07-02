"""Monte Carlo robustness layer on top of a real backtest.

A single backtest gives a single PnL number. That number could be skill or it
could be one lucky path. This module asks the honest follow-up: *if the day had
played out differently but with the same statistical character, how would the
strategy have done?*

Method -- stationary block bootstrap (Politis & Romano, 1994)
------------------------------------------------------------
We take the strategy's **real per-tick PnL increments** and resample them in
*blocks* of random length to build many synthetic equity curves. Resampling in
blocks (rather than one tick at a time) preserves the short-term autocorrelation
and clustering that single-tick resampling would destroy. The spread of the
resulting end-of-day PnLs is a confidence band around the backtested result.

What this is and isn't
----------------------
This is a robustness / luck test on the *observed* PnL stream -- simple, fast and
transparent. It does **not** re-simulate the order book or let the strategy
re-trade (that would be a full synthetic-market Monte Carlo, a heavier and more
assumption-laden thing). So it captures path/sequencing risk, not "what if my
fills had been different". The assumption -- that PnL increments are roughly
stationary and resamplable in blocks -- is stated plainly so the numbers aren't
oversold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np


def stationary_bootstrap_indices(T: int, n_paths: int, mean_block: float,
                                 rng: np.random.Generator) -> np.ndarray:
    """Index matrix (n_paths, T) for a stationary block bootstrap.

    At each step we either advance to the next tick (prob 1 - 1/mean_block) or
    jump to a fresh random tick (prob 1/mean_block), with wrap-around. Block
    lengths are therefore geometric with mean ``mean_block``.
    """
    p = 1.0 / mean_block
    idx = np.empty((n_paths, T), dtype=np.int64)
    cur = rng.integers(0, T, size=n_paths)
    for t in range(T):
        idx[:, t] = cur
        restart = rng.random(n_paths) < p
        cur = np.where(restart, rng.integers(0, T, size=n_paths), (cur + 1) % T)
    return idx


@dataclass
class MonteCarloResult:
    n_paths: int
    mean_block: int
    finals: np.ndarray                 # end-of-session PnL, one per path
    timestamps: List[int]              # x-axis for the fan chart
    bands: dict                        # percentile -> equity curve over time
    observed_final: float              # the real backtest's end PnL

    mean: float = 0.0
    std: float = 0.0
    p05: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    prob_loss: float = 0.0
    var95: float = 0.0                 # 95% Value-at-Risk (a positive loss number)
    cvar95: float = 0.0               # expected shortfall beyond the VaR
    sharpe_mean: float = 0.0
    sharpe_lo: float = 0.0            # 5th pct of bootstrapped daily Sharpe
    sharpe_hi: float = 0.0           # 95th pct

    def summary_lines(self) -> List[str]:
        L = [
            f"Monte Carlo ({self.n_paths} paths, stationary block bootstrap, "
            f"mean block {self.mean_block} ticks)",
            f"  observed backtest PnL : {self.observed_final:>12.0f}",
            f"  bootstrap mean +/- sd : {self.mean:>12.0f}  +/- {self.std:.0f}",
            f"  5th / 50th / 95th pct : {self.p05:>12.0f} / {self.p50:.0f} / {self.p95:.0f}",
            f"  probability of a down day : {self.prob_loss:>8.1%}",
            f"  95% VaR (loss) / CVaR : {self.var95:>12.0f} / {self.cvar95:.0f}"
            "   (negative = even the tail is profitable)",
            f"  daily Sharpe 90% CI   : [{self.sharpe_lo:.2f}, {self.sharpe_hi:.2f}] "
            f"(mean {self.sharpe_mean:.2f})",
        ]
        return L


def monte_carlo_from_increments(increments, observed_final: float, timestamps=None,
                                n_paths: int = 500, mean_block: int = 50,
                                seed: int = 0, var_level: float = 0.95) -> MonteCarloResult:
    d = np.asarray(increments, dtype=float)
    T = d.size
    rng = np.random.default_rng(seed)

    idx = stationary_bootstrap_indices(T, n_paths, mean_block, rng)
    sampled = d[idx]                       # (n_paths, T) resampled increments
    equity = np.cumsum(sampled, axis=1)    # synthetic equity curves
    finals = equity[:, -1]

    # bootstrapped daily Sharpe: mean/std of each path's increments, scaled
    mu = sampled.mean(axis=1)
    sd = sampled.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(sd > 1e-12, mu / sd * np.sqrt(T), 0.0)

    q = (1.0 - var_level)
    var = -np.quantile(finals, q)                          # loss at the q-tail
    tail = finals[finals <= np.quantile(finals, q)]
    cvar = -tail.mean() if tail.size else var

    pct = [5, 25, 50, 75, 95]
    bands = {p: np.percentile(equity, p, axis=0) for p in pct}

    if timestamps is None:
        timestamps = list(range(T))

    return MonteCarloResult(
        n_paths=n_paths, mean_block=mean_block, finals=finals,
        timestamps=list(timestamps), bands=bands, observed_final=observed_final,
        mean=float(finals.mean()), std=float(finals.std(ddof=1)),
        p05=float(np.percentile(finals, 5)), p50=float(np.percentile(finals, 50)),
        p95=float(np.percentile(finals, 95)),
        prob_loss=float(np.mean(finals < 0)),
        var95=float(var), cvar95=float(cvar),
        sharpe_mean=float(sharpe.mean()),
        sharpe_lo=float(np.percentile(sharpe, 5)),
        sharpe_hi=float(np.percentile(sharpe, 95)),
    )


def monte_carlo_backtest(result, n_paths: int = 500, mean_block: int = 50,
                         seed: int = 0) -> MonteCarloResult:
    """Convenience wrapper: bootstrap a :class:`BacktestResult`'s PnL path."""
    eq = np.asarray(result.total_pnl, dtype=float)
    increments = np.diff(eq)
    return monte_carlo_from_increments(
        increments, observed_final=float(eq[-1]),
        timestamps=result.timestamps[1:], n_paths=n_paths,
        mean_block=mean_block, seed=seed,
    )
