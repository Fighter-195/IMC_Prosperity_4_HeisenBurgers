from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
import math

# =========================================================================
# PARAMETERS
# =========================================================================
PEPPER_LIMIT = 80
OSMIUM_LIMIT = 80

# KALMAN PARAMETERS: The "Stubborn Filter" (Tracks the Macro Drift)
KALMAN_Q = 5e-05  
KALMAN_R = 0.2  

# AR2 RESIDUAL PARAMETERS (Predicts the Micro-Structure Bounces)
PHI1 = -0.45
PHI2 = 0.4

# RISK & EXECUTION SETTINGS
MR_STRENGTH = 0.5       # How aggressively we trade the predicted AR(2) convergence
BASE_INV_RISK = 0.01

class Trader:
    def __init__(self):
        self.history = {}

    def run(self, state: TradingState):
        # =========================================================================
        # RESTORE STATE
        # =========================================================================
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                loaded_history = saved.get('history', {})
                for k, v in loaded_history.items():
                    self.history[k] = v
            except Exception:
                pass

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
            # 🌶️ INTARIAN PEPPER ROOT: PURE BUY & HOLD
            # ============================================================
            if product == 'INTARIAN_PEPPER_ROOT':
                buy_capacity = PEPPER_LIMIT - current_pos
                
                if buy_capacity > 0:
                    vol_at_ask = abs(order_depth.sell_orders[best_ask])
                    take_vol = min(buy_capacity, vol_at_ask)
                    if take_vol > 0:
                        orders.append(Order(product, best_ask, take_vol))

                if orders:
                    result[product] = orders
                continue

            # ============================================================
            # 🔥 OSMIUM: KALMAN DRIFT + AR(2) MICRO-TIMING
            # ============================================================
            if product == "ASH_COATED_OSMIUM":
                buy_capacity = OSMIUM_LIMIT - current_pos
                sell_capacity = -OSMIUM_LIMIT - current_pos

                # ------------------------------------------------------------
                # 1. RAW MID PRICE
                # ------------------------------------------------------------
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total

                # ------------------------------------------------------------
                # 2. KALMAN FILTER UPDATE (The Macro Drift)
                # ------------------------------------------------------------
                if "KALMAN_STATE" not in self.history:
                    self.history["KALMAN_STATE"] = {"x": mid, "P": 1.0}

                k_state = self.history["KALMAN_STATE"]
                x_prev = k_state["x"]
                P_prev = k_state["P"]

                x_pred = x_prev
                P_pred = P_prev + KALMAN_Q
                K = P_pred / (P_pred + KALMAN_R)  
                kalman_fair = x_pred + K * (mid - x_pred) 
                P_new = (1 - K) * P_pred          

                self.history["KALMAN_STATE"]["x"] = kalman_fair
                self.history["KALMAN_STATE"]["P"] = P_new

                # ------------------------------------------------------------
                # 3. AR(2) RESIDUAL PREDICTION (The Micro-Timing Alpha)
                # ------------------------------------------------------------
                # Calculate current residual (how far are we from the Kalman anchor?)
                current_residual = mid - kalman_fair
                
                if "OSMIUM_RESIDUALS" not in self.history:
                    self.history["OSMIUM_RESIDUALS"] = [0.0, 0.0]
                
                res_hist = self.history["OSMIUM_RESIDUALS"]
                
                # Predict the next residual using AR(2) logic
                # PHI1 is usually negative (mean reversion), PHI2 is positive (momentum)
                pred_residual = (PHI1 * res_hist[-1]) + (PHI2 * res_hist[-2])
                
                # Update history for the next tick
                res_hist.append(current_residual)
                if len(res_hist) > 2:
                    res_hist.pop(0)

                # ------------------------------------------------------------
                # 4. FINAL FAIR PRICE & CUBIC RISK MULTIPLIER
                # ------------------------------------------------------------
                # Blend the Kalman Anchor with the predicted AR(2) bounce
                statistical_fair = kalman_fair + (pred_residual * MR_STRENGTH)

                urgency = abs(current_pos) / OSMIUM_LIMIT
                risk_multiplier = 1 + ((urgency ** 3) * 4) 
                dynamic_inv_risk = BASE_INV_RISK * risk_multiplier
                
                # Shift our statistical fair price away from our risk
                fair_price = statistical_fair - (current_pos * dynamic_inv_risk)

                # ------------------------------------------------------------
                # 5. ELASTIC PEG (Anchored to Kalman, not 10k)
                # ------------------------------------------------------------
                peg_stretch = int(abs(current_pos) / 20)
                dynamic_peg = int(round(kalman_fair)) # Failsafe tracks the Kalman, no 10k lockups

                # ------------------------------------------------------------
                # 6. TAKER LOGIC: The Dual Sniper
                # ------------------------------------------------------------
                EDGE = 1.0
                
                stretch_buy = peg_stretch if current_pos < 0 else 0
                stretch_sell = peg_stretch if current_pos > 0 else 0
                
                take_bid_threshold = max(fair_price - EDGE, dynamic_peg - 1 + stretch_buy)
                take_ask_threshold = min(fair_price + EDGE, dynamic_peg + 1 - stretch_sell)

                if order_depth.sell_orders:
                    for ask_price, vol in sorted(order_depth.sell_orders.items()):
                        if ask_price <= take_bid_threshold and buy_capacity > 0:
                            trade_vol = min(abs(vol), buy_capacity)
                            if trade_vol > 0:
                                orders.append(Order(product, ask_price, trade_vol))
                                buy_capacity -= trade_vol

                if order_depth.buy_orders:
                    for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                        if bid_price >= take_ask_threshold and sell_capacity < 0:
                            trade_vol = max(-abs(vol), sell_capacity)
                            if trade_vol < 0:
                                orders.append(Order(product, bid_price, trade_vol))
                                sell_capacity -= trade_vol

                # ------------------------------------------------------------
                # 7. MAKER LOGIC: Laddering around Fair Price
                # ------------------------------------------------------------
                pb = int(math.floor(best_bid if best_bid else fair_price) + 1)
                pa = int(math.ceil(best_ask if best_ask else fair_price) - 1)

                is_crossed = pb >= pa
                is_overpaying = pb > fair_price
                is_underselling = pa < fair_price

                if is_crossed or is_overpaying or is_underselling:
                    ideal_bid = math.floor(fair_price - 1)
                    ideal_ask = math.ceil(fair_price + 1)
                    
                    pb = min(ideal_bid, best_ask - 1) if best_ask else ideal_bid
                    pa = max(ideal_ask, best_bid + 1) if best_bid else ideal_ask
                    
                    if best_bid and best_ask and pb >= pa:
                        pb = best_bid
                        pa = best_ask

                # THE DYNAMIC KALMAN FAILSAFE
                if current_pos > 0:
                    pa = max(pa, dynamic_peg + 1 - peg_stretch)
                    pb = min(pb, dynamic_peg - 1)
                elif current_pos < 0:
                    pb = min(pb, dynamic_peg - 1 + peg_stretch)
                    pa = max(pa, dynamic_peg + 1)
                else:
                    pb = min(pb, dynamic_peg - 1)
                    pa = max(pa, dynamic_peg + 1)

                # Final Queue Safety
                pb = min(pb, best_ask - 1) if best_ask else pb
                pa = max(pa, best_bid + 1) if best_bid else pa

                # VOLUME LADDERING EXECUTION
                if buy_capacity > 0:
                    t1_vol = min(buy_capacity, 40)
                    orders.append(Order(product, pb, t1_vol))
                    buy_capacity -= t1_vol
                    
                    if buy_capacity > 0:
                        orders.append(Order(product, pb - 2, buy_capacity))

                if sell_capacity < 0:
                    t1_vol = max(sell_capacity, -40)
                    orders.append(Order(product, pa, t1_vol))
                    sell_capacity -= t1_vol
                    
                    if sell_capacity < 0:
                        orders.append(Order(product, pa + 2, sell_capacity))

                if orders:
                    result[product] = orders

        # SAVE STATE
        state_to_save = {
            'history': self.history
        }
        
        return result, 0, json.dumps(state_to_save)