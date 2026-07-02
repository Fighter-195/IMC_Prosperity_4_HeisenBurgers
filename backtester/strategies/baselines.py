"""Baseline strategies with a clean, parameterised interface.

Each strategy is a class exposing ``run(state) -> (orders, conversions,
traderData)`` so it drops straight into the backtester (and into the real
Prosperity sandbox).  Parameters live in ``__init__`` so the walk-forward
optimiser can tune them.  Fair value is estimated from a rolling mid rather
than hard-coded, so nothing here secretly "knows" a product's peg.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Dict, List

from pqlab.datamodel import Order, TradingState
from pqlab.data import LIMITS, DEFAULT_LIMIT


def _best_bid_ask(od):
    bid = max(od.buy_orders) if od.buy_orders else None
    ask = min(od.sell_orders) if od.sell_orders else None
    return bid, ask


def _microprice(od):
    """Volume-weighted mid: leans toward the side with more size behind it."""
    bid, ask = _best_bid_ask(od)
    if bid is None or ask is None:
        return None
    bv = od.buy_orders[bid]
    av = abs(od.sell_orders[ask])
    tot = bv + av
    if tot == 0:
        return (bid + ask) / 2
    return (bid * av + ask * bv) / tot


class _Stateful:
    """Mixin: roll a per-product mid history through traderData."""

    def __init__(self, window: int):
        self.window = window
        self.hist: Dict[str, deque] = {}

    def _restore(self, state: TradingState):
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                for k, v in saved.get("hist", {}).items():
                    self.hist[k] = deque(v, maxlen=self.window)
            except Exception:
                pass

    def _dump(self) -> str:
        return json.dumps({"hist": {k: list(v) for k, v in self.hist.items()}})

    def _push(self, product: str, value: float) -> deque:
        dq = self.hist.get(product)
        if dq is None:
            dq = deque(maxlen=self.window)
            self.hist[product] = dq
        dq.append(value)
        return dq


class FixedSpreadMM(_Stateful):
    """Symmetric maker: quote ``size`` at fair +/- ``edge`` around a rolling
    fair value, capped by the position limit."""

    name = "fixed_spread_mm"

    def __init__(self, edge: int = 2, size: int = 20, window: int = 50, products=None):
        super().__init__(window)
        self.edge = edge
        self.size = size
        self.products = products

    def run(self, state: TradingState):
        self._restore(state)
        result: Dict[str, List[Order]] = {}
        for product, od in state.order_depths.items():
            if self.products and product not in self.products:
                continue
            mp = _microprice(od)
            if mp is None:
                continue
            hist = self._push(product, mp)
            fair = sum(hist) / len(hist)

            pos = state.position.get(product, 0)
            limit = LIMITS.get(product, DEFAULT_LIMIT)
            buy_cap = limit - pos
            sell_cap = limit + pos

            orders: List[Order] = []
            if buy_cap > 0:
                orders.append(Order(product, int(fair - self.edge), min(self.size, buy_cap)))
            if sell_cap > 0:
                orders.append(Order(product, int(fair + self.edge), -min(self.size, sell_cap)))
            if orders:
                result[product] = orders
        return result, 0, self._dump()


class InventorySkewMM(_Stateful):
    """Maker that skews both quotes against its inventory so the book pulls
    itself back toward flat.  This is the strategy we walk-forward optimise.

    Params:
        edge   -- base half-spread in ticks
        skew   -- ticks of quote shift per unit of (position/limit)
        size   -- quote size per side
    """

    name = "inventory_skew_mm"

    def __init__(self, edge: int = 2, skew: float = 3.0, size: int = 25, window: int = 40, products=None):
        super().__init__(window)
        self.edge = edge
        self.skew = skew
        self.size = size
        self.products = products

    def run(self, state: TradingState):
        self._restore(state)
        result: Dict[str, List[Order]] = {}
        for product, od in state.order_depths.items():
            if self.products and product not in self.products:
                continue
            mp = _microprice(od)
            if mp is None:
                continue
            hist = self._push(product, mp)
            fair = sum(hist) / len(hist)

            pos = state.position.get(product, 0)
            limit = LIMITS.get(product, DEFAULT_LIMIT)
            # shift quotes opposite to inventory
            shift = self.skew * (pos / limit)
            bid_px = int(round(fair - self.edge - shift))
            ask_px = int(round(fair + self.edge - shift))

            buy_cap = limit - pos
            sell_cap = limit + pos
            orders: List[Order] = []
            if buy_cap > 0:
                orders.append(Order(product, bid_px, min(self.size, buy_cap)))
            if sell_cap > 0:
                orders.append(Order(product, ask_px, -min(self.size, sell_cap)))
            if orders:
                result[product] = orders
        return result, 0, self._dump()


class MeanReversionTaker(_Stateful):
    """Taker: when the microprice deviates from its rolling mean by more than
    ``entry`` ticks, fade the move (buy dips, sell rips) by crossing the book."""

    name = "mean_reversion_taker"

    def __init__(self, window: int = 100, entry: float = 3.0, size: int = 15, products=None):
        super().__init__(window)
        self.entry = entry
        self.size = size
        self.products = products

    def run(self, state: TradingState):
        self._restore(state)
        result: Dict[str, List[Order]] = {}
        for product, od in state.order_depths.items():
            if self.products and product not in self.products:
                continue
            mp = _microprice(od)
            if mp is None:
                continue
            hist = self._push(product, mp)
            if len(hist) < self.window:
                continue
            mean = sum(hist) / len(hist)
            dev = mp - mean

            pos = state.position.get(product, 0)
            limit = LIMITS.get(product, DEFAULT_LIMIT)
            bid, ask = _best_bid_ask(od)
            orders: List[Order] = []
            if dev < -self.entry and ask is not None:  # cheap -> buy the ask
                cap = limit - pos
                if cap > 0:
                    orders.append(Order(product, ask, min(self.size, cap)))
            elif dev > self.entry and bid is not None:  # rich -> sell the bid
                cap = limit + pos
                if cap > 0:
                    orders.append(Order(product, bid, -min(self.size, cap)))
            if orders:
                result[product] = orders
        return result, 0, self._dump()
