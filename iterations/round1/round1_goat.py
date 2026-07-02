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
PHI1_INIT = -0.45
PHI2_INIT = 0.35
LR = 0.03
BASE_INV_RISK = 0.02

LONG_WINDOW = 40
MR_STRENGTH = 0.4

class Trader:
    def __init__(self):
        self.history = {
            "ASH_COATED_OSMIUM": [],
        }
        self.phi1 = PHI1_INIT
        self.phi2 = PHI2_INIT

    def run(self, state: TradingState):
        # RESTORE STATE
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                loaded_history = saved.get('history', {})
                for k, v in loaded_history.items():
                    self.history[k] = v
                self.phi1 = saved.get('phi1', self.phi1)
                self.phi2 = saved.get('phi2', self.phi2)
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
            # 🌶️ INTARIAN PEPPER ROOT: STAGGERED ACCUMULATE & HOLD
            # ============================================================
            if product == 'INTARIAN_PEPPER_ROOT':
                
                buy_capacity = PEPPER_LIMIT - current_pos
                
                # We only buy if we aren't full. We NEVER sell (Hold until auto-settle).
                if buy_capacity > 0:
                    
                    # 1. Grab only the absolute Best Ask (No sweeping the book)
                    vol_at_best_ask = abs(order_depth.sell_orders[best_ask])
                    
                    # 2. THE NEW IF STATEMENT: Only affect the initial entry
                    if current_pos == 0:
                        take_vol = min(vol_at_best_ask, buy_capacity, 10) # Tiny initial bite
                    else:
                        take_vol = min(vol_at_best_ask, buy_capacity, 20) # Normal staggered buy
                    
                    if take_vol > 0:
                        orders.append(Order(product, best_ask, take_vol))

                if orders:
                    result[product] = orders
# ============================================================
#                 🔥 OSMIUM (DYNAMIC AR2 + SPREAD-ADAPTIVE FAILSAFE)
#              ============================================================
            elif product == "ASH_COATED_OSMIUM":
                    buy_capacity = OSMIUM_LIMIT - current_pos
                    sell_capacity = -OSMIUM_LIMIT - current_pos

                    # ---------------- MARKET GEOMETRY ----------------
                    bid_vol = order_depth.buy_orders[best_bid]
                    ask_vol = abs(order_depth.sell_orders[best_ask])
                    total = bid_vol + ask_vol
                    mid = (best_bid * ask_vol + best_ask * bid_vol) / total
                    
                    spread = best_ask - best_bid # <-- NEW: Track the spread width
                    
                    # ---------------- HISTORY ----------------
                    hist = self.history["ASH_COATED_OSMIUM"]
                    hist.append(mid)
                    if len(hist) > 100: hist.pop(0)

                    # ---------------- AR(2) LEARNING (VOLATILITY GATED) ----------------
                    if len(hist) >= 4:
                        p0, p1, p2, p3 = hist[-1], hist[-2], hist[-3], hist[-4]
                        x0, x1, x2 = p0 - p1, p1 - p2, p2 - p3

                        pred = self.phi1 * x1 + self.phi2 * x2
                        err = x0 - pred
                        norm = (x1**2 + x2**2)

                        if norm > 1.0: 
                            self.phi1 += (LR * err * x1) / (norm + 1e-6)
                            self.phi2 += (LR * err * x2) / (norm + 1e-6)

                            self.phi1 = max(-1.0, min(1.0, self.phi1))
                            self.phi2 = max(-1.0, min(1.0, self.phi2))

                    # ---------------- PREDICTION & REVERSION ----------------
                    ar2_pred = 0
                    if len(hist) >= 3:
                        ar2_pred = self.phi1 * (hist[-1] - hist[-2]) + self.phi2 * (hist[-2] - hist[-3])

                    long_mean = sum(hist[-LONG_WINDOW:]) / len(hist[-LONG_WINDOW:]) if len(hist) >= LONG_WINDOW else mid
                    mr_term = (long_mean - mid) * MR_STRENGTH

                    # ---------------- SIGNAL REGIME ----------------
                    w_ar, w_mr = (0.8, 0.2) if abs(ar2_pred) > 1.5 else (0.4, 0.6)
                    raw_signal = (w_ar * ar2_pred) + (w_mr * mr_term)

                    # ============================================================
                    # 🛡️ THE FAILSAFE: ADAPTIVE MAKER EDGES
                    # ============================================================
                    is_flatline = abs(raw_signal) < 0.4 
                    
                    if is_flatline:
                        obi = (bid_vol - ask_vol) / total
                        fair_price = mid + (obi * 1.5)
                        
                        taker_edge = 1.0 
                        # NEW: The edge dynamically scales with the spread. 
                        # If the spread is 16, we demand an edge of 4.
                        maker_edge = spread * 0.25 
                    else:
                        fair_price = mid + raw_signal
                        taker_edge = 1.0
                        maker_edge = max(0.5, spread * 0.2) # Floor at 0.5, but scale if wide

                    # ---------------- TAKER ----------------
                    if order_depth.sell_orders:
                        for ask_price, vol in sorted(order_depth.sell_orders.items()):
                            if ask_price <= fair_price - taker_edge and buy_capacity > 0:
                                trade_vol = min(abs(vol), buy_capacity)
                                if trade_vol > 0:
                                    orders.append(Order(product, ask_price, trade_vol))
                                    buy_capacity -= trade_vol

                    if order_depth.buy_orders:
                        for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                            if bid_price >= fair_price + taker_edge and sell_capacity < 0:
                                trade_vol = max(-abs(vol), sell_capacity)
                                if trade_vol < 0:
                                    orders.append(Order(product, bid_price, trade_vol))
                                    sell_capacity -= trade_vol

                    # ---------------- MAKER (PROTECTED) ----------------
                    best_possible_bid = math.floor(fair_price - maker_edge)
                    best_possible_ask = math.ceil(fair_price + maker_edge)
                    
                    # Prevent crossing our own orders by enforcing a strict 1-tick gap between bid/ask
                    pb = int(min(best_bid + 1, best_possible_bid))
                    pa = int(max(best_ask - 1, best_possible_ask))
                    
                    if pa <= pb:
                        pa = pb + 1

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
            'phi2': self.phi2
        }
        
        return result, 0, json.dumps(state_to_save)