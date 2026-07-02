"""Parameter search with honest in-sample / out-of-sample separation.

The whole point of a backtester is to *not* fool yourself.  A grid search that
reports the best in-sample number is how people overfit; the walk-forward
routine here always selects parameters on one day and reports the result on a
*different, later* day, so the headline figure is genuinely out-of-sample.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence

from pqlab.backtester import Backtester
from pqlab.data import load_day
from pqlab.metrics import compute_metrics


def _param_combos(grid: Dict[str, Sequence]):
    keys = list(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, values))


def evaluate(strategy_factory: Callable, days: Sequence, metric: str = "final_pnl",
             round_num: int = 2, match_mode="all") -> float:
    """Run a freshly built strategy over each day (fresh state per day, as in
    the real sandbox) and aggregate the chosen metric.

    ``final_pnl`` aggregates by sum; anything else by mean.
    """
    bt = Backtester(match_mode=match_mode)
    vals = []
    for d in days:
        day = load_day(round_num, d)
        res = bt.run(strategy_factory(), day)
        m = compute_metrics(res)
        vals.append(getattr(m, metric))
    if metric == "final_pnl":
        return float(sum(vals))
    return float(sum(vals) / len(vals))


@dataclass
class GridResult:
    params: Dict
    train_score: float
    test_score: float


def grid_search(strategy_cls, grid: Dict[str, Sequence], train_days: Sequence,
                test_days: Sequence, metric: str = "final_pnl", round_num: int = 2,
                fixed: Dict = None) -> List[GridResult]:
    """Score every parameter combo on the train days, then re-score each on the
    test days.  Returned list is sorted by train score (descending) so the
    caller can see how the in-sample winner holds up out-of-sample."""
    fixed = fixed or {}
    results: List[GridResult] = []
    for params in _param_combos(grid):
        full = {**fixed, **params}
        train = evaluate(lambda p=full: strategy_cls(**p), train_days, metric, round_num)
        test = evaluate(lambda p=full: strategy_cls(**p), test_days, metric, round_num)
        results.append(GridResult(params=params, train_score=train, test_score=test))
    results.sort(key=lambda r: r.train_score, reverse=True)
    return results


@dataclass
class WalkForwardStep:
    train_day: int
    test_day: int
    best_params: Dict
    train_score: float
    test_score: float


def walk_forward(strategy_cls, grid: Dict[str, Sequence], days: Sequence,
                 metric: str = "final_pnl", round_num: int = 2,
                 fixed: Dict = None) -> List[WalkForwardStep]:
    """Classic walk-forward: for consecutive day pairs, pick the best params on
    the earlier day and lock them in for the later day.  The test scores are
    what an honest backtest would actually have earned."""
    fixed = fixed or {}
    steps: List[WalkForwardStep] = []
    ordered = list(days)
    for train_day, test_day in zip(ordered[:-1], ordered[1:]):
        ranked = grid_search(strategy_cls, grid, [train_day], [test_day],
                             metric=metric, round_num=round_num, fixed=fixed)
        best = ranked[0]
        steps.append(WalkForwardStep(
            train_day=train_day, test_day=test_day,
            best_params=best.params, train_score=best.train_score,
            test_score=best.test_score,
        ))
    return steps
