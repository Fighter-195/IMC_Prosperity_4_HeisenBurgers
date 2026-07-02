"""Differential validation of our matcher against the reference engine.

We don't just *claim* our from-scratch matcher reproduces Prosperity's rules --
we prove it.  This script imports the actual matching functions from the cloned
``prosperity3bt`` reference simulator and runs thousands of randomised
order/book/market-trade scenarios through both engines, asserting identical
fills, position deltas and cash deltas.

If the reference repo isn't importable (different machine / no clone), the
script says so and exits cleanly rather than failing the build.
"""

from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from types import SimpleNamespace

# --- locate the cloned reference simulator -------------------------------- #
REF_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "Imc_prosperity", "imc-prosperity-4", "backtester"),
    os.path.join(os.path.expanduser("~"), "documents", "Imc_prosperity",
                 "imc-prosperity-4", "backtester"),
]


def _load_reference():
    for path in REF_CANDIDATES:
        if os.path.isdir(os.path.join(path, "prosperity3bt")):
            sys.path.insert(0, path)
            try:
                from prosperity3bt.runner import match_buy_order, match_sell_order
                from prosperity3bt.models import MarketTrade, TradeMatchingMode
                from prosperity3bt.datamodel import Order as RefOrder, OrderDepth as RefOD, Trade as RefTrade
                return dict(match_buy_order=match_buy_order, match_sell_order=match_sell_order,
                            MarketTrade=MarketTrade, TradeMatchingMode=TradeMatchingMode,
                            Order=RefOrder, OrderDepth=RefOD, Trade=RefTrade)
            except Exception as e:  # pragma: no cover
                print(f"[skip] found reference at {path} but import failed: {e}")
                return None
    return None


# project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pqlab.backtester import Backtester, _MarketTrade
from pqlab.datamodel import Order, OrderDepth


def random_scenario(rng: random.Random):
    base = rng.randint(80, 120)
    od_buy, od_sell = {}, {}
    for k in range(rng.randint(1, 3)):
        od_buy[base - 1 - k] = rng.randint(1, 30)
    for k in range(rng.randint(1, 3)):
        od_sell[base + 1 + k] = rng.randint(1, 30)
    side = rng.choice(["buy", "sell"])
    # order price chosen to sometimes cross, sometimes rest
    price = base + rng.randint(-4, 4)
    qty = rng.randint(1, 40)
    n_mt = rng.randint(0, 3)
    mkt = [(base + rng.randint(-4, 4), rng.randint(1, 20)) for _ in range(n_mt)]
    return base, od_buy, od_sell, side, price, qty, mkt


def run_reference(ref, od_buy, od_sell, side, price, qty, mkt):
    od = ref["OrderDepth"]()
    od.buy_orders = dict(od_buy)
    od.sell_orders = {p: -v for p, v in od_sell.items()}
    state = SimpleNamespace(order_depths={"X": od}, position={}, timestamp=0)
    data = SimpleNamespace(profit_loss=defaultdict(float))
    market = [ref["MarketTrade"](ref["Trade"]("X", p, q, "", "", 0), q, q) for p, q in mkt]
    order = ref["Order"]("X", price, qty if side == "buy" else -qty)
    fn = ref["match_buy_order"] if side == "buy" else ref["match_sell_order"]
    trades = fn(state, data, order, market, ref["TradeMatchingMode"].all)
    fills = sorted((t.price, t.quantity) for t in trades)
    return fills, state.position.get("X", 0), data.profit_loss["X"]


def run_ours(od_buy, od_sell, side, price, qty, mkt):
    od = OrderDepth()
    od.buy_orders = dict(od_buy)
    od.sell_orders = {p: -v for p, v in od_sell.items()}
    market = [_MarketTrade(p, q, q) for p, q in mkt]
    position, cash = {}, {}
    bt = Backtester()
    order = Order("X", price, qty if side == "buy" else -qty)
    if side == "buy":
        trades = bt._match_buy(order, od, market, position, cash)
    else:
        trades = bt._match_sell(order, od, market, position, cash)
    fills = sorted((t.price, t.quantity) for t in trades)
    return fills, position.get("X", 0), cash.get("X", 0.0)


def main(n: int = 5000, seed: int = 7):
    ref = _load_reference()
    if ref is None:
        print("[skip] reference simulator (prosperity3bt) not found -- "
              "clone chrispyroberts/imc-prosperity-4 next to this repo to enable "
              "differential validation.")
        return
    rng = random.Random(seed)
    mismatches = 0
    for _ in range(n):
        base, od_buy, od_sell, side, price, qty, mkt = random_scenario(rng)
        r = run_reference(ref, od_buy, od_sell, side, price, qty, mkt)
        o = run_ours(od_buy, od_sell, side, price, qty, mkt)
        if r != o:
            mismatches += 1
            if mismatches <= 5:
                print("MISMATCH")
                print("  scenario:", side, "price", price, "qty", qty,
                      "buy", od_buy, "sell", od_sell, "mkt", mkt)
                print("  reference:", r)
                print("  ours     :", o)
    if mismatches == 0:
        print(f"OK  {n} randomised scenarios -- our matcher is identical to the "
              f"reference engine (fills, position, cash).")
    else:
        print(f"FAIL  {mismatches}/{n} scenarios diverged.")
        sys.exit(1)


if __name__ == "__main__":
    main()
