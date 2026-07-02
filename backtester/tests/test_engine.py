"""Unit + invariant tests for the matching engine.

Runs under pytest, or directly:  python tests/test_engine.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pqlab.backtester import Backtester, BacktestResult, MatchMode, _MarketTrade
from pqlab.datamodel import Order, OrderDepth
from pqlab.data import DayData, PriceRow, LIMITS, load_day
from pqlab.metrics import per_product_pnl
from strategies import make


def _od(buys=None, sells=None):
    od = OrderDepth()
    od.buy_orders = dict(buys or {})
    od.sell_orders = {p: -v for p, v in (sells or {}).items()}
    return od


def test_buy_lifts_book_cheapest_first():
    bt = Backtester()
    od = _od(sells={100: 10, 101: 10})
    pos, cash = {}, {}
    fills = bt._match_buy(Order("X", 101, 15), od, [], pos, cash)
    assert sorted((t.price, t.quantity) for t in fills) == [(100, 10), (101, 5)]
    assert pos["X"] == 15
    assert cash["X"] == -(100 * 10 + 101 * 5)


def test_buy_respects_limit_price():
    bt = Backtester()
    od = _od(sells={102: 10})           # cheapest ask above our price
    pos, cash = {}, {}
    fills = bt._match_buy(Order("X", 101, 5), od, [], pos, cash)
    assert fills == [] and pos == {} and cash == {}


def test_passive_buy_fills_via_market_trade_at_own_price():
    bt = Backtester()  # mode 'all'
    od = _od(sells={105: 10})           # book won't cross
    mkt = [_MarketTrade(price=99, buy_capacity=3, sell_capacity=3)]
    pos, cash = {}, {}
    fills = bt._match_buy(Order("X", 100, 8), od, mkt, pos, cash)
    # fills at OUR price 100, capped by the trade's sell capacity (3)
    assert [(t.price, t.quantity) for t in fills] == [(100, 3)]
    assert pos["X"] == 3 and cash["X"] == -300


def test_sell_symmetry():
    bt = Backtester()
    od = _od(buys={100: 10, 99: 10})
    pos, cash = {}, {}
    fills = bt._match_sell(Order("X", 99, -12), od, [], pos, cash)  # sells are negative qty
    assert sorted((t.price, t.quantity) for t in fills) == [(99, 2), (100, 10)]
    assert pos["X"] == -12
    assert cash["X"] == (100 * 10 + 99 * 2)


def test_match_mode_none_blocks_passive_fills():
    bt = Backtester(match_mode=MatchMode.none)
    od = _od(sells={105: 10})
    mkt = [_MarketTrade(99, 3, 3)]
    pos, cash = {}, {}
    fills = bt._match_buy(Order("X", 100, 8), od, mkt, pos, cash)
    assert fills == []


def _one_tick_day(orders_product="X", limit_product="X"):
    """A minimal 2-tick day with one product so we can drive Backtester.run."""
    pr0 = PriceRow(0, "X", [100], [50], [101], [50], 100.5)
    pr1 = PriceRow(100, "X", [100], [50], [101], [50], 100.5)
    return DayData(round_num=9, day_num=0, products=["X"], timestamps=[0, 100],
                   prices={0: {"X": pr0}, 100: {"X": pr1}}, trades={})


def test_position_limit_rejects_oversized_orders():
    day = _one_tick_day()

    class Greedy:
        def run(self, state):
            # try to buy 999 -- far beyond the default limit of 50
            return {"X": [Order("X", 101, 999)]}, 0, ""

    res = Backtester().run(Greedy(), day)
    assert res.n_limit_breaches >= 1
    assert all(abs(p) <= day.limit("X") for p in res.position_series["X"])


def test_pnl_identity_and_limits_on_real_data():
    day = load_day(2, 0)
    res = Backtester().run(make("flagship_hybrid"), day)
    # marked total equals the sum of per-product marked PnL
    assert abs(res.final_pnl - sum(per_product_pnl(res).values())) < 1e-6
    # positions never breach the limit at any tick
    for p, series in res.position_series.items():
        lim = LIMITS.get(p, 50)
        assert max(abs(x) for x in series) <= lim


def test_monte_carlo_bootstrap():
    import numpy as np
    from pqlab.montecarlo import monte_carlo_from_increments, stationary_bootstrap_indices

    rng = np.random.default_rng(0)
    idx = stationary_bootstrap_indices(100, 20, 10, rng)
    assert idx.shape == (20, 100)
    assert idx.min() >= 0 and idx.max() < 100

    # constant positive increments -> every resampled session is identical & profitable
    mc = monte_carlo_from_increments(np.full(200, 5.0), observed_final=1000.0,
                                     n_paths=100, mean_block=20)
    assert mc.prob_loss == 0.0
    assert abs(mc.p50 - 1000.0) < 1e-6


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
