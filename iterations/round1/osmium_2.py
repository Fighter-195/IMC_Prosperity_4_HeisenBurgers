from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
import math

# =========================================================================
# PARAMETERS
# =========================================================================
PEPPER_LIMIT = 80
OSMIUM_LIMIT = 80
FV = 10000  # The Absolute Peg for Osmium

# KALMAN PARAMETERS: The "Stubborn Filter"
KALMAN_Q = 1e-05  
KALMAN_R = 0.2  

# BAYESIAN VARIANCE WINDOW (Replaces Mean Reversion Window)
VAR_WINDOW = 40

# RISK: High-Courage Inventory Management
BASE_INV_RISK = 0.01

class Trader:
    def __init__(self):
        self.history = {
            "ASH_COATED_OSMIUM": []
        }

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

        if "ASH_COATED_OSMIUM" not in self.history: 
            self.history["ASH_COATED_OSMIUM"] = []

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

            # # ============================================================
            # # 🌶️ INTARIAN PEPPER ROOT: PURE BUY & HOLD
            # # ============================================================
            # if product == 'INTARIAN_PEPPER_ROOT':
            #     buy_capacity = PEPPER_LIMIT - current_pos
                
            #     if buy_capacity > 0:
            #         vol_at_ask = abs(order_depth.sell_orders[best_ask])
            #         take_vol = min(buy_capacity, vol_at_ask)
            #         if take_vol > 0:
            #             orders.append(Order(product, best_ask, take_vol))

            #     if orders:
            #         result[product] = orders
                
            #     continue

# ============================================================
            # 🔥 OSMIUM: BAYESIAN + KALMAN HYBRID
            # ============================================================
            if product == "ASH_COATED_OSMIUM":
                buy_capacity = OSMIUM_LIMIT - current_pos
                sell_capacity = -OSMIUM_LIMIT - current_pos

                # ------------------------------------------------------------
                # 1. RAW MID PRICE & HISTORY
                # ------------------------------------------------------------
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total
                
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                if len(hist) > VAR_WINDOW: hist.pop(0)

                # ------------------------------------------------------------
                # 2. KALMAN FILTER UPDATE
                # ------------------------------------------------------------
                if "KALMAN_STATE" not in self.history:
                    self.history["KALMAN_STATE"] = {"x": mid, "P": 1.0}

                k_state = self.history["KALMAN_STATE"]
                x_prev = k_state["x"]
                P_prev = k_state["P"]

                x_pred = x_prev
                P_pred = P_prev + KALMAN_Q
                K = P_pred / (P_pred + KALMAN_R)  
                x_new = x_pred + K * (mid - x_pred) 
                P_new = (1 - K) * P_pred          

                self.history["KALMAN_STATE"]["x"] = x_new
                self.history["KALMAN_STATE"]["P"] = P_new

                kalman_fair = x_new
                kalman_variance = max(1e-6, P_new) # Uncertainty of Kalman

                # ------------------------------------------------------------
                # 3. BAYESIAN CONFIDENCE WEIGHTING
                # ------------------------------------------------------------
                # Calculate the rolling variance (uncertainty) of the raw mid price
                if len(hist) > 1:
                    mean_mid = sum(hist) / len(hist)
                    var_mid = sum((x - mean_mid)**2 for x in hist) / (len(hist) - 1)
                    var_mid = max(1e-6, var_mid)
                else:
                    var_mid = 1.0 # Default baseline variance

                # Precision is the inverse of variance. High variance = Low precision (low trust)
                prec_mid = 1.0 / var_mid
                prec_kalman = 1.0 / kalman_variance
                total_prec = prec_mid + prec_kalman

                # Calculate dynamic Bayesian weights
                w_mid = prec_mid / total_prec
                w_kalman = prec_kalman / total_prec

                # The ultimate blended fair price
                bayesian_fair = (w_mid * mid) + (w_kalman * kalman_fair)
                
                # ------------------------------------------------------------
                # 4. CUBIC RISK MULTIPLIER
                # ------------------------------------------------------------
                urgency = abs(current_pos) / OSMIUM_LIMIT
                risk_multiplier = 1 + ((urgency ** 3) * 4) 
                dynamic_inv_risk = BASE_INV_RISK * risk_multiplier
                
                # Shift our bayesian fair price away from our risk
                fair_price = bayesian_fair - (current_pos * dynamic_inv_risk)

                # ------------------------------------------------------------
                # ELASTIC PEG CALCULATION (Anti-Lockup)
                # ------------------------------------------------------------
                peg_stretch = int(abs(current_pos) / 20)

                # ------------------------------------------------------------
                # 5. TAKER LOGIC: The Dual Sniper (Volume-Weighted Spread)
                # ------------------------------------------------------------
                # Calculate Volume-Weighted VWAP for the top 3 levels of the book
                bid_vwap_num, bid_vwap_den = 0, 0
                for p, v in sorted(order_depth.buy_orders.items(), reverse=True)[:3]:
                    bid_vwap_num += p * v
                    bid_vwap_den += v
                bid_vwap = bid_vwap_num / bid_vwap_den if bid_vwap_den > 0 else best_bid

                ask_vwap_num, ask_vwap_den = 0, 0
                for p, v in sorted(order_depth.sell_orders.items())[:3]:
                    v_abs = abs(v)
                    ask_vwap_num += p * v_abs
                    ask_vwap_den += v_abs
                ask_vwap = ask_vwap_num / ask_vwap_den if ask_vwap_den > 0 else best_ask

                # Volume-Weighted Spread ignores 1-lot spoofing noise
                vws = ask_vwap - bid_vwap
                
                # Dynamic EDGE based on the true volume weight
                EDGE = max(1.0, 0.5 * vws)
                
                stretch_buy = peg_stretch if current_pos < 0 else 0
                stretch_sell = peg_stretch if current_pos > 0 else 0
                
                take_bid_threshold = max(fair_price - EDGE, FV - 1 + stretch_buy)
                take_ask_threshold = min(fair_price + EDGE, FV + 1 - stretch_sell)

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
                # 6. MAKER LOGIC: Bypass + Elastic Peg + Laddering
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

                # THE ELASTIC 10K FAILSAFE
                if current_pos > 0:
                    pa = max(pa, FV + 1 - peg_stretch)
                    pb = min(pb, FV - 1)
                elif current_pos < 0:
                    pb = min(pb, FV - 1 + peg_stretch)
                    pa = max(pa, FV + 1)
                else:
                    pb = min(pb, FV - 1)
                    pa = max(pa, FV + 1)

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