from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple
import json
import math

# =========================================================================
# PARAMETERS
# =========================================================================
PEPPER_LIMIT = 80
OSMIUM_LIMIT = 80

ROUND_END = 99900

# OSMIUM PARAMS
PHI1_INIT = -0.35
PHI2_INIT = 0.35
LR = 0.02
INV_RISK = 0.03

LONG_WINDOW = 50
MR_STRENGTH = 0.25

class Trader:
    def __init__(self):
        self.history = {
            "ASH_COATED_OSMIUM": [],
        }
        self.phi1 = PHI1_INIT
        self.phi2 = PHI2_INIT

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():

            # ============================================================
            # 🔥 PEPPER (UNCHANGED OP STRATEGY)
            # ============================================================
            if product == "INTARIAN_PEPPER_ROOT":

                current_pos = state.position.get(product, 0)
                orders: List[Order] = []

                if state.timestamp < ROUND_END:
                    buy_capacity = PEPPER_LIMIT - current_pos

                    if buy_capacity > 0:
                        for ask_price, vol in sorted(order_depth.sell_orders.items()):
                            if buy_capacity <= 0:
                                break
                            trade_vol = min(abs(vol), buy_capacity)
                            if trade_vol > 0:
                                orders.append(Order(product, ask_price, trade_vol))
                                buy_capacity -= trade_vol

                        if buy_capacity > 0 and order_depth.buy_orders:
                            best_bid = max(order_depth.buy_orders.keys())
                            orders.append(Order(product, best_bid + 1, buy_capacity))

                else:
                    if current_pos > 0:
                        for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                            if current_pos <= 0:
                                break
                            trade_vol = max(-abs(vol), -current_pos)
                            if trade_vol < 0:
                                orders.append(Order(product, bid_price, trade_vol))
                                current_pos += trade_vol

                        if current_pos > 0 and order_depth.buy_orders:
                            best_bid = max(order_depth.buy_orders.keys())
                            orders.append(Order(product, best_bid, -current_pos))

                if orders:
                    result[product] = orders

            # ============================================================
            # 🔥 OSMIUM (AR2 + MEAN REVERSION HYBRID)
            # ============================================================
            elif product == "ASH_COATED_OSMIUM":

                if not order_depth.buy_orders and not order_depth.sell_orders:
                    continue

                current_pos = state.position.get(product, 0)
                buy_capacity = OSMIUM_LIMIT - current_pos
                sell_capacity = -OSMIUM_LIMIT - current_pos

                orders: List[Order] = []

                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

                # ---------------- MID PRICE ----------------
                if best_bid and best_ask:
                    bid_vol = order_depth.buy_orders[best_bid]
                    ask_vol = abs(order_depth.sell_orders[best_ask])
                    total = bid_vol + ask_vol
                    mid = (best_bid * ask_vol + best_ask * bid_vol) / total
                elif best_bid:
                    mid = float(best_bid)
                elif best_ask:
                    mid = float(best_ask)
                else:
                    continue

                # ---------------- HISTORY ----------------
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                if len(hist) > 100:
                    hist.pop(0)

                # ---------------- AR(2) LEARNING ----------------
                if len(hist) >= 4:
                    p0, p1, p2, p3 = hist[-1], hist[-2], hist[-3], hist[-4]

                    x0 = p0 - p1
                    x1 = p1 - p2
                    x2 = p2 - p3

                    pred = self.phi1 * x1 + self.phi2 * x2
                    err = x0 - pred

                    norm = (x1**2 + x2**2) + 1e-6

                    self.phi1 += (LR * err * x1) / norm
                    self.phi2 += (LR * err * x2) / norm

                    self.phi1 = max(-1, min(1, self.phi1))
                    self.phi2 = max(-1, min(1, self.phi2))

                # ---------------- AR2 PREDICTION ----------------
                ar2_pred = 0
                if len(hist) >= 3:
                    p0, p1, p2 = hist[-1], hist[-2], hist[-3]
                    ar2_pred = self.phi1 * (p0 - p1) + self.phi2 * (p1 - p2)

                # ---------------- MEAN REVERSION ----------------
                if len(hist) >= LONG_WINDOW:
                    long_mean = sum(hist[-LONG_WINDOW:]) / LONG_WINDOW
                else:
                    long_mean = mid

                mr_term = (long_mean - mid) * MR_STRENGTH

                # ---------------- REGIME WEIGHTING ----------------
                if abs(ar2_pred) > 1.5:
                    w_ar = 0.8
                    w_mr = 0.2
                else:
                    w_ar = 0.4
                    w_mr = 0.6

                fair_price = mid + (w_ar * ar2_pred) + (w_mr * mr_term)
                fair_price -= current_pos * INV_RISK

                # ---------------- TAKER ----------------
                EDGE = 1.0

                if order_depth.sell_orders:
                    for ask_price, vol in sorted(order_depth.sell_orders.items()):
                        if ask_price <= fair_price - EDGE and buy_capacity > 0:
                            trade_vol = min(abs(vol), buy_capacity)
                            if trade_vol > 0:
                                orders.append(Order(product, ask_price, trade_vol))
                                buy_capacity -= trade_vol

                if order_depth.buy_orders:
                    for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                        if bid_price >= fair_price + EDGE and sell_capacity < 0:
                            trade_vol = max(-abs(vol), sell_capacity)
                            if trade_vol < 0:
                                orders.append(Order(product, bid_price, trade_vol))
                                sell_capacity -= trade_vol

                # ---------------- MAKER ----------------
                pb = int((best_bid or fair_price) + 1)
                pa = int((best_ask or fair_price) - 1)

                if buy_capacity > 0:
                    orders.append(Order(product, pb, min(20, buy_capacity)))

                if sell_capacity < 0:
                    orders.append(Order(product, pa, max(-20, sell_capacity)))

                if orders:
                    result[product] = orders

        return result, 0, ""