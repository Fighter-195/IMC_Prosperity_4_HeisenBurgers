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
# KALMAN PARAMETERS (Tune these to change responsiveness)
KALMAN_Q = 1e-4  # Process Noise (Low = assumes true price moves slowly)
KALMAN_R = 0.01  # Measurement Noise (Higher = distrusts sudden market spikes more)

# OSMIUM PARAMS
PHI1_INIT = -0.45
PHI2_INIT = 0.4
LR = 0.03
BASE_INV_RISK = 0.02

LONG_WINDOW = 50
MR_STRENGTH = 0.3

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
            # 🔥 OSMIUM (FIXED KALMAN + OBI + DEGREE 2 SKEW)
            # ============================================================
            if product == "ASH_COATED_OSMIUM":
                # --- NEW CONSTANTS ---
                MAX_QUOTE_SIZE = 20 # Your request to trade 20 units
                CHUNK_SIZE = 20

                # 1. Capacity with Chunking logic
                buy_capacity = min(CHUNK_SIZE, OSMIUM_LIMIT - current_pos)
                sell_capacity = max(-CHUNK_SIZE, -OSMIUM_LIMIT - current_pos)

                # ---------------- MID PRICE & OBI ----------------
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total_vol = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total_vol
                
                obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0

                # ---------------- HISTORY (For Mean Reversion) ----------------
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                if len(hist) > LONG_WINDOW: hist.pop(0)

                # ---------------- KALMAN FILTER ----------------
                if "KALMAN_STATE" not in self.history:
                    self.history["KALMAN_STATE"] = {"x": mid, "P": 1.0}

                k_state = self.history["KALMAN_STATE"]
                x_prev, P_prev = k_state["x"], k_state["P"]

                P_pred = P_prev + KALMAN_Q
                K = P_pred / (P_pred + KALMAN_R)
                x_new = x_prev + K * (mid - x_prev)
                P_new = (1 - K) * P_pred

                self.history["KALMAN_STATE"]["x"] = x_new
                self.history["KALMAN_STATE"]["P"] = P_new

                # ---------------- FAIR PRICE MATH ----------------
                if len(hist) >= LONG_WINDOW:
                    long_mean = sum(hist[-LONG_WINDOW:]) / LONG_WINDOW
                else:
                    long_mean = mid

                mr_term = (long_mean - x_new) * MR_STRENGTH
                obi_term = obi * 1.5 

                fair_price = x_new + mr_term + obi_term
                
                # ---------------- DYNAMIC INVENTORY RISK (DEGREE 2) ----------------
                urgency_signed = current_pos / OSMIUM_LIMIT
                urgency = abs(urgency_signed)
                risk_multiplier = 1 + ((urgency ** 2) * 4) 
                
                fair_price -= current_pos * (BASE_INV_RISK * risk_multiplier)
                
                # ---------------- TAKER ----------------
                EDGE = 1.0
                if order_depth.sell_orders:
                    for ask_price, vol in sorted(order_depth.sell_orders.items()):
                        if ask_price <= fair_price - EDGE and buy_capacity > 0:
                            trade_vol = min(abs(vol), buy_capacity)
                            orders.append(Order(product, ask_price, trade_vol))
                            buy_capacity -= trade_vol

                if order_depth.buy_orders:
                    for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                        if bid_price >= fair_price + EDGE and sell_capacity < 0:
                            trade_vol = max(-abs(vol), sell_capacity)
                            orders.append(Order(product, bid_price, trade_vol))
                            sell_capacity -= abs(trade_vol)

                # ---------------- MAKER (FIXED LOGIC) ----------------
                # 1. Base Prices
                pb = int(best_bid + 1)
                pa = int(best_ask - 1)

                # 2. Safety Bypass Check
                if pb >= pa or pb > fair_price or pa < fair_price:
                    pb = min(math.floor(fair_price - 1), best_ask - 1)
                    pa = max(math.ceil(fair_price + 1), best_bid + 1)
                    if pb >= pa: # Extreme spread compression
                        pb, pa = best_bid, best_ask

                # 3. Size Skewing (Degree 2)
                ideal_bid_size = math.ceil(MAX_QUOTE_SIZE * (1.0 - (max(0, urgency_signed) ** 2)))
                ideal_ask_size = math.ceil(MAX_QUOTE_SIZE * (1.0 - (abs(min(0, urgency_signed)) ** 2)))

                # 4. Final Constraints
                final_bid_vol = min(ideal_bid_size, buy_capacity)
                final_ask_vol = max(-ideal_ask_size, sell_capacity)

                if final_bid_vol > 0:
                    orders.append(Order(product, pb, final_bid_vol))
                if final_ask_vol < 0:
                    orders.append(Order(product, pa, final_ask_vol))

            if orders:
                result[product] = orders
        # SAVE STATE
        state_to_save = {
            'history': self.history,
            'phi1': self.phi1,
            'phi2': self.phi2
        }
        
        return result, 0, json.dumps(state_to_save)