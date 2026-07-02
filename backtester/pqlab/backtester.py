"""A from-scratch, event-driven limit-order-book backtester for the IMC
Prosperity market-making environment.

This is our own implementation. It re-creates the matching mechanics that the
Prosperity engine documents and that the community reference simulators use,
so the numbers are comparable, but every line here is written from the data
model up. The point was to *own* the harness end to end: book reconstruction,
order matching, position-limit enforcement and mark-to-market accounting.

Matching model (per timestamp, after ``Trader.run`` returns orders)
-------------------------------------------------------------------
1.  **Position-limit gate.** If a product's submitted orders could push the
    position past ``+limit`` or ``-limit``, *all* of that product's orders are
    rejected for the tick (this is exactly how Prosperity behaves).
2.  **Book channel.** Each surviving order first sweeps the visible book:
    a buy lifts every ask priced at or below its limit (cheapest first); a
    sell hits every bid priced at or above its limit (highest first). Fills
    print at the book price.
3.  **Market-trade channel.** Whatever volume is left rests passively and is
    filled against the market trades that printed this tick, at the order's
    own limit price -- the conservative convention. ``match_trades`` controls
    how aggressive this is:
      * ``all``  -> fill against trades priced equal-to-or-better than us
      * ``worse``-> fill only against trades strictly worse for the taker
      * ``none`` -> passive orders never fill
    For a maker that quotes *inside* the spread this channel is where almost
    all fills come from, so it matters a great deal.

PnL is marked to the mid every tick:  ``pnl = realised_cash + position * mid``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List

from pqlab.data import DayData
from pqlab.datamodel import (
    Listing,
    Observation,
    Order,
    OrderDepth,
    Trade,
    TradingState,
)


class MatchMode(str, Enum):
    all = "all"
    worse = "worse"
    none = "none"


@dataclass
class _MarketTrade:
    """A printed market trade with independent buy/sell capacity counters,
    mirroring how the reference engine lets one print fill both a resting bid
    and a resting ask."""

    price: int
    buy_capacity: int
    sell_capacity: int


@dataclass
class BacktestResult:
    round_num: int
    day_num: int
    products: List[str]
    timestamps: List[int]
    # product -> list of marked PnL, one entry per timestamp
    pnl_series: Dict[str, List[float]] = field(default_factory=dict)
    # list of total marked PnL (summed across products), one per timestamp
    total_pnl: List[float] = field(default_factory=list)
    # product -> list of position, one per timestamp
    position_series: Dict[str, List[int]] = field(default_factory=dict)
    # product -> list of mid price, one per timestamp
    mid_series: Dict[str, List[float]] = field(default_factory=dict)
    own_trades: List[Trade] = field(default_factory=list)
    n_limit_breaches: int = 0

    @property
    def final_pnl(self) -> float:
        return self.total_pnl[-1] if self.total_pnl else 0.0


def _build_order_depth(price_row) -> OrderDepth:
    od = OrderDepth()
    for price, volume in zip(price_row.bid_prices, price_row.bid_volumes):
        od.buy_orders[price] = volume
    for price, volume in zip(price_row.ask_prices, price_row.ask_volumes):
        od.sell_orders[price] = -volume  # Prosperity stores asks as negative
    return od


class Backtester:
    def __init__(self, match_mode: MatchMode = MatchMode.all, enforce_limits: bool = True):
        self.match_mode = MatchMode(match_mode)
        self.enforce_limits = enforce_limits

    # ------------------------------------------------------------------ #
    # order matching
    # ------------------------------------------------------------------ #
    def _match_buy(self, order: Order, od: OrderDepth, mkt: List[_MarketTrade],
                   position: Dict[str, int], cash: Dict[str, float]) -> List[Trade]:
        trades: List[Trade] = []
        remaining = order.quantity  # > 0

        # 1. book channel: lift asks at or below our price, cheapest first
        for price in sorted(p for p in od.sell_orders if p <= order.price):
            avail = abs(od.sell_orders[price])
            vol = min(remaining, avail)
            if vol <= 0:
                continue
            trades.append(Trade(order.symbol, price, vol, "SUBMISSION", "", 0))
            position[order.symbol] = position.get(order.symbol, 0) + vol
            cash[order.symbol] = cash.get(order.symbol, 0.0) - price * vol
            od.sell_orders[price] += vol
            if od.sell_orders[price] == 0:
                del od.sell_orders[price]
            remaining -= vol
            if remaining == 0:
                return trades

        if self.match_mode == MatchMode.none:
            return trades

        # 2. market-trade channel: passive fills at our own price
        for mt in mkt:
            if mt.sell_capacity == 0 or mt.price > order.price:
                continue
            if mt.price == order.price and self.match_mode == MatchMode.worse:
                continue
            vol = min(remaining, mt.sell_capacity)
            if vol <= 0:
                continue
            trades.append(Trade(order.symbol, order.price, vol, "SUBMISSION", "", 0))
            position[order.symbol] = position.get(order.symbol, 0) + vol
            cash[order.symbol] = cash.get(order.symbol, 0.0) - order.price * vol
            mt.sell_capacity -= vol
            remaining -= vol
            if remaining == 0:
                return trades
        return trades

    def _match_sell(self, order: Order, od: OrderDepth, mkt: List[_MarketTrade],
                    position: Dict[str, int], cash: Dict[str, float]) -> List[Trade]:
        trades: List[Trade] = []
        remaining = -order.quantity  # make positive

        # 1. book channel: hit bids at or above our price, highest first
        for price in sorted((p for p in od.buy_orders if p >= order.price), reverse=True):
            avail = od.buy_orders[price]
            vol = min(remaining, avail)
            if vol <= 0:
                continue
            trades.append(Trade(order.symbol, price, vol, "", "SUBMISSION", 0))
            position[order.symbol] = position.get(order.symbol, 0) - vol
            cash[order.symbol] = cash.get(order.symbol, 0.0) + price * vol
            od.buy_orders[price] -= vol
            if od.buy_orders[price] == 0:
                del od.buy_orders[price]
            remaining -= vol
            if remaining == 0:
                return trades

        if self.match_mode == MatchMode.none:
            return trades

        # 2. market-trade channel
        for mt in mkt:
            if mt.buy_capacity == 0 or mt.price < order.price:
                continue
            if mt.price == order.price and self.match_mode == MatchMode.worse:
                continue
            vol = min(remaining, mt.buy_capacity)
            if vol <= 0:
                continue
            trades.append(Trade(order.symbol, order.price, vol, "", "SUBMISSION", 0))
            position[order.symbol] = position.get(order.symbol, 0) - vol
            cash[order.symbol] = cash.get(order.symbol, 0.0) + order.price * vol
            mt.buy_capacity -= vol
            remaining -= vol
            if remaining == 0:
                return trades
        return trades

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #
    def run(self, trader, day: DayData) -> BacktestResult:
        position: Dict[str, int] = {p: 0 for p in day.products}
        cash: Dict[str, float] = {p: 0.0 for p in day.products}

        result = BacktestResult(
            round_num=day.round_num,
            day_num=day.day_num,
            products=list(day.products),
            timestamps=list(day.timestamps),
        )
        for p in day.products:
            result.pnl_series[p] = []
            result.position_series[p] = []
            result.mid_series[p] = []

        trader_data = ""
        # own/market trades carry over one tick, exactly as Prosperity feeds them
        prev_own: Dict[str, List[Trade]] = {}
        prev_market: Dict[str, List[Trade]] = {}

        for ts in day.timestamps:
            order_depths: Dict[str, OrderDepth] = {}
            listings: Dict[str, Listing] = {}
            mids: Dict[str, float] = {}
            for product, row in day.prices[ts].items():
                order_depths[product] = _build_order_depth(row)
                listings[product] = Listing(product, product, 1)
                mids[product] = row.mid_price

            state = TradingState(
                traderData=trader_data,
                timestamp=ts,
                listings=listings,
                order_depths=order_depths,
                own_trades=prev_own,
                market_trades=prev_market,
                position=dict(position),
                observations=Observation({}, {}),
            )

            orders, _conversions, trader_data = trader.run(state)
            trader_data = trader_data or ""

            # market trades available to fill passive orders this tick
            mkt_pool: Dict[str, List[_MarketTrade]] = {}
            for product, tlist in day.trades.get(ts, {}).items():
                mkt_pool[product] = [_MarketTrade(t.price, t.quantity, t.quantity) for t in tlist]

            tick_own: Dict[str, List[Trade]] = {}
            for product in day.products:
                p_orders = orders.get(product, []) if orders else []
                if not p_orders:
                    continue

                if self.enforce_limits and self._breaches_limit(product, p_orders, position, day):
                    result.n_limit_breaches += 1
                    continue

                od = order_depths[product]
                mkt = mkt_pool.get(product, [])
                fills: List[Trade] = []
                for order in p_orders:
                    if order.quantity > 0:
                        fills += self._match_buy(order, od, mkt, position, cash)
                    elif order.quantity < 0:
                        fills += self._match_sell(order, od, mkt, position, cash)
                for f in fills:
                    f.timestamp = ts
                if fills:
                    tick_own[product] = fills
                    result.own_trades.extend(fills)

            # record marked PnL etc.
            total = 0.0
            for product in day.products:
                mid = mids.get(product)
                # A product can be unquoted on a tick (empty book, mid==0 in the
                # raw data). Marking a held position at 0 would invent a huge
                # phantom swing, so carry forward the last valid mid instead.
                if mid is None or mid <= 0:
                    mid = result.mid_series[product][-1] if result.mid_series[product] else 0.0
                pnl = cash[product] + position[product] * mid
                result.pnl_series[product].append(pnl)
                result.position_series[product].append(position[product])
                result.mid_series[product].append(mid)
                total += pnl
            result.total_pnl.append(total)

            # remaining market trades become next tick's market_trades feed
            prev_own = tick_own
            prev_market = {
                product: [Trade(product, mt.price, min(mt.buy_capacity, mt.sell_capacity))
                          for mt in mts if min(mt.buy_capacity, mt.sell_capacity) > 0]
                for product, mts in mkt_pool.items()
            }

        return result

    def _breaches_limit(self, product, p_orders, position, day) -> bool:
        pos = position.get(product, 0)
        total_long = sum(o.quantity for o in p_orders if o.quantity > 0)
        total_short = sum(-o.quantity for o in p_orders if o.quantity < 0)
        limit = day.limit(product)
        return pos + total_long > limit or pos - total_short < -limit
