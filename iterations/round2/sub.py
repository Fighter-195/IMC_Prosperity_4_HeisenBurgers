from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
import math

# =========================================================================
# PARAMETERS
# =========================================================================
PEPPER_LIMIT = 80
OSMIUM_LIMIT = 80
FV = 10000  

KALMAN_Q = 1e-05  
KALMAN_R = 0.2  

# User-Tuned AR(2) Parameters
PHI_1 = -0.45
PHI_2 = 0.4

BASE_INV_RISK = 0.01

class Trader:
    
    def bid(self) -> int:
        return 4100

    def __init__(self):
        self.history = {
            "ASH_COATED_OSMIUM": []
        }

    def run(self, state: TradingState):
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                loaded_history = saved.get('history', {})
                for k,v in loaded_history.items():
                    self.history[k] = v
            except Exception:
                pass

        if "ASH_COATED_OSMIUM" not in self.history: 
            self.history["ASH_COATED_OSMIUM"] = []

        result: Dict[str, List[Order]] = {}

        for product,order_depth in state.order_depths.items():
            
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
            # 🔥 OSMIUM: PURE MAKER & SPREAD FARMER
            # ============================================================
            if product == "ASH_COATED_OSMIUM":
                buy_capacity = OSMIUM_LIMIT - current_pos
                sell_capacity = -OSMIUM_LIMIT - current_pos

                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total
                
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                
                if len(hist) > 20: 
                    hist.pop(0)

                # 1. KALMAN FILTER (The Stable Anchor)
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

                # 2. INVENTORY SKEW (The Rubber Band)
                urgency = abs(current_pos) / OSMIUM_LIMIT
                risk_multiplier = 1 + ((urgency ** 3) * 4) 
                dynamic_inv_risk = BASE_INV_RISK * risk_multiplier
                
                fair_price = kalman_fair - (current_pos * dynamic_inv_risk)

                # 3. NEUTERED TAKER LOGIC: Absolute Arbitrage Only
                # We refuse to cross the massive 16-tick spread unless we are guaranteed an immediate win against 10000.
                peg_stretch = int(abs(current_pos) / 15)
                stretch_buy = peg_stretch if current_pos < 0 else 0
                stretch_sell = peg_stretch if current_pos > 0 else 0

                take_bid_threshold = FV - 1 + stretch_buy
                take_ask_threshold = FV + 1 - stretch_sell

                if order_depth.sell_orders:
                    for ask_price,vol in sorted(order_depth.sell_orders.items()):
                        if ask_price <= take_bid_threshold and buy_capacity > 0:
                            trade_vol = min(abs(vol), buy_capacity)
                            if trade_vol > 0:
                                orders.append(Order(product, ask_price, trade_vol))
                                buy_capacity -= trade_vol

                if order_depth.buy_orders:
                    for bid_price,vol in sorted(order_depth.buy_orders.items(),reverse=True):
                        if bid_price >= take_ask_threshold and sell_capacity < 0:
                            trade_vol = max(-abs(vol), sell_capacity)
                            if trade_vol < 0:
                                orders.append(Order(product, bid_price, trade_vol))
                                sell_capacity -= trade_vol

                # 4. AGGRESSIVE MAKER LOGIC: Queue Priority & Spread Farming
                pb = int(math.floor(best_bid if best_bid else fair_price) + 1)
                pa = int(math.ceil(best_ask if best_ask else fair_price) - 1)

                is_crossed = pb >= pa
                is_overpaying = pb > fair_price
                is_underselling = pa < fair_price

                # If our aggressive queue quote is worse than our fair price, back off
                if is_crossed or is_overpaying or is_underselling:
                    ideal_bid = math.floor(fair_price - 1)
                    ideal_ask = math.ceil(fair_price + 1)
                    
                    pb = min(ideal_bid, best_ask - 1) if best_ask else ideal_bid
                    pa = max(ideal_ask, best_bid + 1) if best_bid else ideal_ask
                    
                    if best_bid and best_ask and pb >= pa:
                        pb = best_bid
                        pa = best_ask

                # 5. THE ELASTIC 10K FAILSAFE
                if current_pos > 0:
                    pa = max(pa, FV + 1 - peg_stretch)
                    pb = min(pb, FV - 1)
                elif current_pos < 0:
                    pb = min(pb, FV - 1 + peg_stretch)
                    pa = max(pa, FV + 1)
                else:
                    pb = min(pb, FV - 1)
                    pa = max(pa, FV + 1)

                pb = min(pb, best_ask - 1) if best_ask else pb
                pa = max(pa, best_bid + 1) if best_bid else pa

                # 6. LADDERED EXECUTION
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

        state_to_save = {
            'history': self.history
        }
        
        return result, 0, json.dumps(state_to_save)