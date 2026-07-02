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

class Trader:
    def __init__(self):
        self.history = {
            "ASH_COATED_OSMIUM": [],
            "INTARIAN_PEPPER_ROOT": []
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

        if "INTARIAN_PEPPER_ROOT" not in self.history: 
            self.history["INTARIAN_PEPPER_ROOT"] = []
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

            # ============================================================
            # 🔥 OSMIUM (V7: ADAPTIVE KALMAN + NON-LINEAR OBI + MOMENTUM)
            # ============================================================
            if product == "ASH_COATED_OSMIUM":
                MAX_QUOTE_SIZE = 20
                CHUNK_SIZE = 20

                buy_capacity = min(CHUNK_SIZE, OSMIUM_LIMIT - current_pos)
                sell_capacity = max(-CHUNK_SIZE, -OSMIUM_LIMIT - current_pos)

                # ---------------- MID ----------------
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total_vol = bid_vol + ask_vol

                mid = (best_bid * ask_vol + best_ask * bid_vol) / total_vol

                # ---------------- HISTORY ----------------
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                if len(hist) > 50:
                    hist.pop(0)

                # ---------------- 1. ADAPTIVE KALMAN ----------------
                if len(hist) >= 5:
                    returns = [hist[i] - hist[i - 1] for i in range(-5, 0)]
                    vol = sum(abs(r) for r in returns) / len(returns)
                else:
                    vol = 1.0

                KALMAN_Q = 1e-4 + 0.001 * vol
                KALMAN_R = 0.01

                if "KALMAN_STATE" not in self.history:
                    self.history["KALMAN_STATE"] = {"x": mid, "P": 1.0}

                k = self.history["KALMAN_STATE"]

                x_pred = k["x"]
                P_pred = k["P"] + KALMAN_Q

                K_gain = P_pred / (P_pred + KALMAN_R)
                x_new = x_pred + K_gain * (mid - x_pred)
                P_new = (1 - K_gain) * P_pred

                self.history["KALMAN_STATE"]["x"] = x_new
                self.history["KALMAN_STATE"]["P"] = P_new

                kalman_fair = x_new

                # ---------------- 3. FIXED MEAN REVERSION ----------------
                LONG_WINDOW = 30
                MR_STRENGTH = 0.5

                if len(hist) >= LONG_WINDOW:
                    long_mean = sum(hist[-LONG_WINDOW:]) / LONG_WINDOW
                else:
                    long_mean = mid

                # Kept as standard multiplier to avoid 10,000 division neutralization
                mr_term = (long_mean - kalman_fair) * MR_STRENGTH

                # ---------------- 5. MOMENTUM CONFIRMATION ----------------
                if len(hist) >= 3:
                    momentum = hist[-1] - hist[-3]
                else:
                    momentum = 0

                momentum_term = 0.3 * momentum

                # ---------------- 2. NON-LINEAR OBI ----------------
                obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0
                obi_term = 2.0 * math.tanh(2 * obi)

                # ---------------- 4. INVENTORY OVER-PENALIZATION FIX ----------------
                BASE_INV_RISK = 0.015

                urgency_signed = current_pos / OSMIUM_LIMIT
                urgency = abs(urgency_signed)

                risk_multiplier = 1 + (urgency * 2)
                dynamic_inv_risk = BASE_INV_RISK * risk_multiplier

                # ---------------- FINAL FAIR PRICE ----------------
                fair_price = (
                    kalman_fair
                    + mr_term
                    + momentum_term
                    + obi_term
                    - current_pos * dynamic_inv_risk
                )

                # ---------------- 6. ADAPTIVE TAKER LOGIC ----------------
                spread = best_ask - best_bid
                EDGE = max(0.8, 0.5 * spread)

                # Buy (take asks)
                if order_depth.sell_orders:
                    for ask_price, vol_ask in sorted(order_depth.sell_orders.items()):
                        if ask_price <= fair_price - EDGE and buy_capacity > 0:
                            trade_vol = min(abs(vol_ask), buy_capacity)
                            if trade_vol > 0:
                                orders.append(Order(product, ask_price, trade_vol))
                                buy_capacity -= trade_vol

                # Sell (take bids)
                if order_depth.buy_orders:
                    for bid_price, vol_bid in sorted(order_depth.buy_orders.items(), reverse=True):
                        if bid_price >= fair_price + EDGE and sell_capacity < 0:
                            trade_vol = max(-abs(vol_bid), sell_capacity)
                            if trade_vol < 0:
                                orders.append(Order(product, bid_price, trade_vol))
                                sell_capacity -= abs(trade_vol) 

                # ---------------- 7. IMPROVED MAKER LOGIC ----------------
                # Safe pennying bounded by fair price
                pb = min(int(best_bid + 1), math.floor(fair_price - 0.5))
                pa = max(int(best_ask - 1), math.ceil(fair_price + 0.5))

                # Failsafe against crossing spreads or inversions
                if pb >= pa or pb > fair_price or pa < fair_price:
                    pb = min(math.floor(fair_price - 0.5), best_ask - 1)
                    pa = max(math.ceil(fair_price + 0.5), best_bid + 1)
                    if pb >= pa: 
                        pb, pa = best_bid, best_ask

                # ---------------- SIZE SKEW (LINEAR) ----------------
                ideal_bid_size = math.ceil(MAX_QUOTE_SIZE * (1.0 - max(0, urgency_signed)))
                ideal_ask_size = math.ceil(MAX_QUOTE_SIZE * (1.0 + min(0, urgency_signed)))

                actual_bid_size = min(ideal_bid_size, buy_capacity)
                actual_ask_size = max(-ideal_ask_size, sell_capacity)

                if actual_bid_size > 0:
                    orders.append(Order(product, pb, actual_bid_size))

                if actual_ask_size < 0:
                    orders.append(Order(product, pa, actual_ask_size))

                # ---------------- SAVE ORDERS ----------------
                if orders:
                    result[product] = orders

        # =========================================================================
        # SAVE STATE & RETURN
        # =========================================================================
        state_to_save = {
            'history': self.history
        }
        
        return result, 0, json.dumps(state_to_save)