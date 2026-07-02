from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple
import json
import math

# =========================================================================
# PARAMETERS
# =========================================================================
PEPPER_LIMIT = 80
OSMIUM_LIMIT = 80

# PEPPER PARAMS
PEPPER_GAMMA = 0.05
PREDICTION_HORIZON = 25
EXIT_START = 98500
FORCE_EXIT = 99950

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
            "INTARIAN_PEPPER_ROOT": []
        }
        self.phi1 = PHI1_INIT
        self.phi2 = PHI2_INIT
        self.pepper_slope = 0.0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        # RESTORE STATE
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                loaded_history = saved.get('history', {})
                for k, v in loaded_history.items():
                    self.history[k] = v
                self.phi1 = saved.get('phi1', self.phi1)
                self.phi2 = saved.get('phi2', self.phi2)
                self.pepper_slope = saved.get('pepper_slope', self.pepper_slope)
            except Exception:
                pass

        if "INTARIAN_PEPPER_ROOT" not in self.history: self.history["INTARIAN_PEPPER_ROOT"] = []
        if "ASH_COATED_OSMIUM" not in self.history: self.history["ASH_COATED_OSMIUM"] = []

        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():
            
            if not order_depth.buy_orders and not order_depth.sell_orders:
                continue

            current_pos = state.position.get(product, 0)
            orders: List[Order] = []

            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
            
            if best_bid is None or best_ask is None:
                continue

            # ============================================================
            # 🌶️ INTARIAN PEPPER ROOT: ZERO-DROP BID CHASER
            # ============================================================
            if product == 'INTARIAN_PEPPER_ROOT':
                mid = (best_bid + best_ask) / 2.0
                
                hist = self.history["INTARIAN_PEPPER_ROOT"]
                hist.append(mid)
                if len(hist) > 200: hist.pop(0)

                if len(hist) >= 2:
                    stable_slope = (hist[-1] - hist[0]) / len(hist)
                    raw_slope = hist[-1] - hist[-2]
                    self.pepper_slope = 0.8 * stable_slope + 0.2 * raw_slope

                buy_cap = PEPPER_LIMIT - current_pos
                sell_cap = -PEPPER_LIMIT - current_pos

                # --------------------------------------------------------
                # PHASE 1: PURE MAKER ACCUMULATION (No Takers Allowed)
                # --------------------------------------------------------
                if state.timestamp < EXIT_START:
                    target_pos = PEPPER_LIMIT
                    future_fair_price = mid + (self.pepper_slope * PREDICTION_HORIZON)
                    res_price = future_fair_price - (PEPPER_GAMMA * (current_pos - target_pos))

                    # Notice how there are no 'for' loops sweeping the book here anymore.
                    # We exclusively rely on Limit Orders to get our fills.

                    target_bid = min(best_bid + 1, math.floor(res_price - 0.5))
                    target_ask = max(best_ask - 1, math.ceil(res_price + 0.5))

                    pb = int(target_bid)
                    pa = int(target_ask)

                    # Ensure we never cross our own spread, or cross the market spread
                    if pb >= pa: pb, pa = int(best_bid), int(best_ask)
                    if pb >= best_ask: pb = int(best_bid)

                    # Place our full capacity on the books. 
                    # If we aren't filled this tick, we simply cancel and move the bid up next tick.
                    if buy_cap > 0: orders.append(Order(product, pb, buy_cap))
                    if sell_cap < 0: orders.append(Order(product, pa, sell_cap))

                # --------------------------------------------------------
                # PHASE 2: MAKER UNWIND (Exit at peak)
                # --------------------------------------------------------
                elif state.timestamp < FORCE_EXIT:
                    if current_pos > 0:
                        ticks_left = (100000 - state.timestamp) / 100
                        final_fair_value = mid + (self.pepper_slope * ticks_left)
                        
                        target_ask = max(best_bid + 1, best_ask - 1)
                        safe_ask = int(max(target_ask, math.floor(final_fair_value) - 1))
                        orders.append(Order(product, safe_ask, -current_pos))

                # --------------------------------------------------------
                # PHASE 3: FAILSAFE DUMP
                # --------------------------------------------------------
                else:
                    if current_pos > 0:
                        for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                            if current_pos <= 0: break
                            trade_vol = max(-abs(vol), -current_pos)
                            orders.append(Order(product, bid_price, trade_vol))
                            current_pos += trade_vol

                if orders: result[product] = orders

            # ============================================================
            # 🔥 OSMIUM (AR2 + MEAN REVERSION HYBRID)
            # ============================================================
            elif product == "ASH_COATED_OSMIUM":
                buy_capacity = OSMIUM_LIMIT - current_pos
                sell_capacity = -OSMIUM_LIMIT - current_pos

                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total
                
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                if len(hist) > 100: hist.pop(0)

                if len(hist) >= 4:
                    p0, p1, p2, p3 = hist[-1], hist[-2], hist[-3], hist[-4]
                    x0, x1, x2 = p0 - p1, p1 - p2, p2 - p3

                    pred = self.phi1 * x1 + self.phi2 * x2
                    err = x0 - pred
                    norm = (x1**2 + x2**2) + 1e-6

                    self.phi1 += (LR * err * x1) / norm
                    self.phi2 += (LR * err * x2) / norm

                    self.phi1 = max(-1, min(1, self.phi1))
                    self.phi2 = max(-1, min(1, self.phi2))

                ar2_pred = 0
                if len(hist) >= 3:
                    p0, p1, p2 = hist[-1], hist[-2], hist[-3]
                    ar2_pred = self.phi1 * (p0 - p1) + self.phi2 * (p1 - p2)

                if len(hist) >= LONG_WINDOW:
                    long_mean = sum(hist[-LONG_WINDOW:]) / LONG_WINDOW
                else:
                    long_mean = mid

                mr_term = (long_mean - mid) * MR_STRENGTH

                if abs(ar2_pred) > 1.5:
                    w_ar, w_mr = 0.8, 0.2
                else:
                    w_ar, w_mr = 0.4, 0.6

                fair_price = mid + (w_ar * ar2_pred) + (w_mr * mr_term)
                fair_price -= current_pos * INV_RISK

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

                pb = int((best_bid or fair_price) + 1)
                pa = int((best_ask or fair_price) - 1)

                if pb >= pa: pb, pa = int(best_bid), int(best_ask)

                if buy_capacity > 0:
                    orders.append(Order(product, pb, min(20, buy_capacity)))
                if sell_capacity < 0:
                    orders.append(Order(product, pa, max(-20, sell_capacity)))

                if orders:
                    result[product] = orders

        # SAVE STATE
        state_to_save = {
            'history': self.history,
            'phi1': self.phi1,
            'phi2': self.phi2,
            'pepper_slope': self.pepper_slope
        }
        
        return result, 0, json.dumps(state_to_save)