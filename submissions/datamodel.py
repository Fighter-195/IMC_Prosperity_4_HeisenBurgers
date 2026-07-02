"""Prosperity-compatible data model.

These classes mirror the objects the IMC Prosperity engine hands to a
``Trader.run(state)`` method, so strategies written for the competition run
against our backtester unmodified (only the import path changes).

Kept deliberately dependency-free: a strategy only ever needs ``Order``,
``OrderDepth`` and ``TradingState`` plus the standard library.
"""

from __future__ import annotations

from typing import Dict, List, Optional

Symbol = str
Product = str
Position = int
Time = int
UserId = str


class Listing:
    def __init__(self, symbol: Symbol, product: Product, denomination: int = 1):
        self.symbol = symbol
        self.product = product
        self.denomination = denomination


class Order:
    """A single order: positive quantity = buy, negative = sell."""

    def __init__(self, symbol: Symbol, price: int, quantity: int) -> None:
        self.symbol = symbol
        self.price = price
        self.quantity = quantity

    def __repr__(self) -> str:
        return f"Order({self.symbol}, {self.price}, {self.quantity})"


class OrderDepth:
    """One side-of-book snapshot.

    ``buy_orders``  maps price -> volume   (volume > 0)
    ``sell_orders`` maps price -> volume   (volume < 0, Prosperity convention)
    """

    def __init__(self) -> None:
        self.buy_orders: Dict[int, int] = {}
        self.sell_orders: Dict[int, int] = {}


class Trade:
    def __init__(
        self,
        symbol: Symbol,
        price: int,
        quantity: int,
        buyer: Optional[UserId] = None,
        seller: Optional[UserId] = None,
        timestamp: int = 0,
    ) -> None:
        self.symbol = symbol
        self.price = price
        self.quantity = quantity
        self.buyer = buyer
        self.seller = seller
        self.timestamp = timestamp

    def __repr__(self) -> str:
        return f"Trade({self.symbol}, p={self.price}, q={self.quantity}, {self.buyer}<-{self.seller}, t={self.timestamp})"


class Observation:
    """Minimal observation container (conversion products are out of scope here)."""

    def __init__(self, plainValueObservations: Dict = None, conversionObservations: Dict = None) -> None:
        self.plainValueObservations = plainValueObservations or {}
        self.conversionObservations = conversionObservations or {}


class TradingState:
    def __init__(
        self,
        traderData: str,
        timestamp: Time,
        listings: Dict[Symbol, Listing],
        order_depths: Dict[Symbol, OrderDepth],
        own_trades: Dict[Symbol, List[Trade]],
        market_trades: Dict[Symbol, List[Trade]],
        position: Dict[Product, Position],
        observations: Observation,
    ) -> None:
        self.traderData = traderData
        self.timestamp = timestamp
        self.listings = listings
        self.order_depths = order_depths
        self.own_trades = own_trades
        self.market_trades = market_trades
        self.position = position
        self.observations = observations
