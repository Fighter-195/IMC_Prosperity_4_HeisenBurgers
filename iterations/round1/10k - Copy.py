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
            # 🔥 OSMIUM (AR2 + MEAN REVERSION HYBRID)
            # ============================================================
    # ============================================================
            # 🔥 OSMIUM (KALMAN FILTER + MEAN REVERSION HYBRID)
            # ============================================================
            if product == "ASH_COATED_OSMIUM":
                buy_capacity = OSMIUM_LIMIT - current_pos
                sell_capacity = -OSMIUM_LIMIT - current_pos

                # ---------------- MID PRICE ----------------
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total
                
                # ---------------- HISTORY (For Mean Reversion) ----------------
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                if len(hist) > LONG_WINDOW: hist.pop(0)

                # ---------------- KALMAN FILTER STATE ----------------
                # We need to initialize the Kalman state if it doesn't exist
                if "KALMAN_STATE" not in self.history:
                    self.history["KALMAN_STATE"] = {"x": mid, "P": 1.0}

                k_state = self.history["KALMAN_STATE"]
                x_prev = k_state["x"]
                P_prev = k_state["P"]

                # ---------------- KALMAN MATH ----------------
                # 1. Predict Step (Assume price stays same, but uncertainty grows)
                x_pred = x_prev
                P_pred = P_prev + KALMAN_Q

                # 2. Update Step (Incorporate new mid-price observation)
                K = P_pred / (P_pred + KALMAN_R)  # Kalman Gain
                x_new = x_pred + K * (mid - x_pred) # The new smoothed true price
                P_new = (1 - K) * P_pred          # The new uncertainty

                # Save state for the next tick
                self.history["KALMAN_STATE"]["x"] = x_new
                self.history["KALMAN_STATE"]["P"] = P_new

                kalman_fair = x_new

                # ---------------- MEAN REVERSION ----------------
                if len(hist) >= LONG_WINDOW:
                    long_mean = sum(hist[-LONG_WINDOW:]) / LONG_WINDOW
                else:
                    long_mean = mid

                mr_term = (long_mean - kalman_fair) * MR_STRENGTH

                # Calculate final blended fair price
                fair_price = kalman_fair + mr_term
                
                # ---------------- DYNAMIC INVENTORY RISK ----------------
                urgency = abs(current_pos) / OSMIUM_LIMIT
                risk_multiplier = 1 + ((urgency ** 3) * 4) 
                dynamic_inv_risk = BASE_INV_RISK * risk_multiplier
                
                fair_price -= current_pos * dynamic_inv_risk
                
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

                # ---------------- MAKER (WITH BYPASS LOGIC) ----------------
                # 1. BASE LOGIC: Aggressive Pennying
                pb = int((best_bid or fair_price) + 1)
                pa = int((best_ask or fair_price) - 1)

                # 2. BYPASS TRIGGER: Detect Faults
                is_crossed = pb >= pa
                is_overpaying = pb > fair_price
                is_underselling = pa < fair_price

                if is_crossed or is_overpaying or is_underselling:
                    # --- BYPASS ALGORITHM ---
                    # Anchor quotes safely to our Kalman Fair Price
                    ideal_bid = math.floor(fair_price - 1)
                    ideal_ask = math.ceil(fair_price + 1)
                    
                    pb = min(ideal_bid, best_ask - 1)
                    pa = max(ideal_ask, best_bid + 1)
                    
                    # Absolute failsafe
                    if pb >= pa:
                        pb = best_bid
                        pa = best_ask

                if buy_capacity > 0:
                    orders.append(Order(product, pb, min(20, buy_capacity)))

                if sell_capacity < 0:
                    orders.append(Order(product, pa, max(-20, sell_capacity)))

            # ==========================================
            # RESULT SAVE
            # ==========================================
            if orders:
                result[product] = orders
        # SAVE STATE
        state_to_save = {
            'history': self.history,
            'phi1': self.phi1,
            'phi2': self.phi2
        }
        
        return result, 0, json.dumps(state_to_save)