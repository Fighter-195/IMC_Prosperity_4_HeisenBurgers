"""Loader for IMC Prosperity market-data CSVs.

Two file types per (round, day):

* ``prices_round_R_day_D.csv``  -- semicolon separated, one row per
  (timestamp, product) with up to three levels of book depth plus the mid.
* ``trades_round_R_day_D.csv``  -- the market trades that printed, used to
  fill resting (passive) maker orders.

Everything is parsed once into plain Python dicts keyed by timestamp so the
backtester can replay a day with no further I/O.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Dict, List

from pqlab.datamodel import Trade

# Position limits. The Prosperity-4 tutorial products were renamed in this
# dataset; both carry a limit of 80 (matches the values used in the original
# competition submissions).
LIMITS: Dict[str, int] = {
    "ASH_COATED_OSMIUM": 80,
    "INTARIAN_PEPPER_ROOT": 80,
}
DEFAULT_LIMIT = 50

DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


@dataclass
class PriceRow:
    timestamp: int
    product: str
    bid_prices: List[int]
    bid_volumes: List[int]
    ask_prices: List[int]
    ask_volumes: List[int]
    mid_price: float


@dataclass
class DayData:
    round_num: int
    day_num: int
    products: List[str]
    timestamps: List[int]
    # timestamp -> product -> PriceRow
    prices: Dict[int, Dict[str, PriceRow]]
    # timestamp -> product -> list[Trade]
    trades: Dict[int, Dict[str, List[Trade]]] = field(default_factory=dict)

    def limit(self, product: str) -> int:
        return LIMITS.get(product, DEFAULT_LIMIT)


def _parse_levels(row: Dict[str, str], side: str):
    """Pull the up-to-3 price/volume levels for a side ('bid' or 'ask')."""
    prices, volumes = [], []
    for i in (1, 2, 3):
        p = row.get(f"{side}_price_{i}", "")
        v = row.get(f"{side}_volume_{i}", "")
        if p == "" or p is None:
            break
        prices.append(int(float(p)))
        volumes.append(int(float(v)))
    return prices, volumes


def load_prices(path: str) -> "tuple[Dict[int, Dict[str, PriceRow]], List[str]]":
    prices: Dict[int, Dict[str, PriceRow]] = {}
    products: List[str] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            ts = int(row["timestamp"])
            product = row["product"]
            if product not in products:
                products.append(product)
            bid_p, bid_v = _parse_levels(row, "bid")
            ask_p, ask_v = _parse_levels(row, "ask")
            pr = PriceRow(
                timestamp=ts,
                product=product,
                bid_prices=bid_p,
                bid_volumes=bid_v,
                ask_prices=ask_p,
                ask_volumes=ask_v,
                mid_price=float(row["mid_price"]),
            )
            prices.setdefault(ts, {})[product] = pr
    return prices, products


def load_trades(path: str) -> Dict[int, Dict[str, List[Trade]]]:
    trades: Dict[int, Dict[str, List[Trade]]] = {}
    if not os.path.exists(path):
        return trades
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            ts = int(row["timestamp"])
            symbol = row["symbol"]
            t = Trade(
                symbol=symbol,
                price=int(float(row["price"])),
                quantity=int(float(row["quantity"])),
                buyer=row.get("buyer") or "",
                seller=row.get("seller") or "",
                timestamp=ts,
            )
            trades.setdefault(ts, {}).setdefault(symbol, []).append(t)
    return trades


def load_day(round_num: int, day_num: int) -> DayData:
    """Load one (round, day) into a :class:`DayData`."""
    folder = os.path.join(DATA_ROOT, f"round{round_num}")
    price_path = os.path.join(folder, f"prices_round_{round_num}_day_{day_num}.csv")
    trade_path = os.path.join(folder, f"trades_round_{round_num}_day_{day_num}.csv")

    prices, products = load_prices(price_path)
    trades = load_trades(trade_path)
    timestamps = sorted(prices.keys())

    return DayData(
        round_num=round_num,
        day_num=day_num,
        products=products,
        timestamps=timestamps,
        prices=prices,
        trades=trades,
    )
