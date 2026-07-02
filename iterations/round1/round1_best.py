from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple
import math
import json

# =========================================================================
# HYPERPARAMETERS
# =========================================================================
# PEPPER ROOT (TARGET INVENTORY MM)
PEPPER_LIMIT = 80
PEPPER_GAMMA = 0.05       # Risk Aversion for Target Inventory
EXIT_START = 98500        # Start unwinding passively
FORCE_EXIT = 99950        # Panic dump if we still hold bags

# OSMIUM (AR2 + BB HYBRID)
OSMIUM_LIMIT = 80
AR2_LR = 0.02
BB_ALPHA = 0.5
BB_THRESH = 2.5
BB_WINDOW = 20
LONG_WINDOW = 50
MR_STRENGTH = 0.25

# OSMIUM MARKET MAKING
OSMIUM_GAMMA = 0.035      # Base inventory penalty
OBI_SENSITIVITY = 1.0     # How much imbalance shifts our quotes
MIN_SPREAD = 1.0

class Trader:
    def __init__(self):
        self.history: Dict[str, List[float]] = {"ASH_COATED_OSMIUM": [], "INTARIAN_PEPPER_ROOT": []}
        self.bb_history: List[float] = []
        self.phi1 = -0.35
        self.phi2 = 0.35
        self.pepper_slope = 0.0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        # 1. RESTORE STATE
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                self.history = saved.get('history', self.history)
                self.bb_history = saved.get('bb_history', self.bb_history)
                self.phi1 = saved.get('phi1', self.phi1)
                self.phi2 = saved.get('phi2', self.phi2)
                self.pepper_slope = saved.get('pepper_slope', self.pepper_slope)
            except Exception:
                pass

        all_orders: Dict[str, List[Order]] = {}

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            current_pos = state.position.get(product, 0)
            product_orders: List[Order] = []

            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
            
            if best_bid is None or best_ask is None:
                continue

            # ============================================================
            # 🌶️ INTARIAN PEPPER ROOT: TARGET INVENTORY MM
            # ============================================================
            if product == 'INTARIAN_PEPPER_ROOT':
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total_vol = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total_vol

                hist_pep = self.history["INTARIAN_PEPPER_ROOT"]
                hist_pep.append(mid)
                if len(hist_pep) > 100: hist_pep.pop(0)

                # Update Slope
                if len(hist_pep) >= 2:
                    raw_slope = hist_pep[-1] - hist_pep[-2]
                    raw_slope = max(-2.0, min(2.0, raw_slope)) # Clamp noise
                    self.pepper_slope = 0.2 * raw_slope + 0.8 * self.pepper_slope

                # PHASE 1: TARGET INVENTORY MARKET MAKING
                if state.timestamp < EXIT_START:
                    fair_price = mid + self.pepper_slope
                    
                    # Target +80 in an uptrend, -80 in a downtrend (though Pepper is usually +)
                    target_pos = PEPPER_LIMIT if self.pepper_slope >= 0 else -PEPPER_LIMIT
                    
                    # Avellaneda-Stoikov variant
                    res_price = fair_price - PEPPER_GAMMA * (current_pos - target_pos)

                    # Add minor OBI shift to protect against toxic sweeps
                    obi = (bid_vol - ask_vol) / total_vol
                    res_price += obi * 0.5

                    target_bid = res_price - 0.5
                    target_ask = res_price + 0.5

                    buy_cap = PEPPER_LIMIT - current_pos
                    sell_cap = -PEPPER_LIMIT - current_pos

                    # Aggressive Taker Logic (Cross spread if mispriced)
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if ask_price <= res_price - 1.0 and buy_cap > 0:
                            take_vol = min(abs(order_depth.sell_orders[ask_price]), buy_cap)
                            product_orders.append(Order(product, ask_price, take_vol))
                            buy_cap -= take_vol

                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_price >= res_price + 1.0 and sell_cap < 0:
                            take_vol = max(-order_depth.buy_orders[bid_price], sell_cap)
                            product_orders.append(Order(product, bid_price, take_vol))
                            sell_cap -= take_vol

                    # Passive Maker Logic
                    pb = int(min(math.floor(target_bid), best_ask - 1))
                    pa = int(max(math.ceil(target_ask), best_bid + 1))

                    if pb >= pa: pb, pa = int(best_bid), int(best_ask)

                    if buy_cap > 0: product_orders.append(Order(product, pb, buy_cap))
                    if sell_cap < 0: product_orders.append(Order(product, pa, sell_cap))

                # PHASE 2: MAKER UNWIND (Sell exactly at the Peak)
                elif state.timestamp < FORCE_EXIT:
                    if current_pos > 0:
                        slope = (hist_pep[-1] - hist_pep[0]) / len(hist_pep) if len(hist_pep) > 1 else 0
                        ticks_left = (100000 - state.timestamp) / 100
                        final_fair_value = mid + (slope * ticks_left)
                        
                        target_ask = max(best_bid + 1, best_ask - 1)
                        safe_ask = int(max(target_ask, math.floor(final_fair_value) - 1))
                        product_orders.append(Order(product, safe_ask, -current_pos))

                # PHASE 3: FAILSAFE DUMP
                else:
                    if current_pos > 0:
                        for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                            if current_pos <= 0: break
                            trade_vol = max(-abs(vol), -current_pos)
                            product_orders.append(Order(product, bid_price, trade_vol))
                            current_pos += trade_vol

            # ============================================================
            # 💎 ASH COATED OSMIUM: ELITE HYBRID MM (UNCHANGED)
            # ============================================================
            elif product == 'ASH_COATED_OSMIUM':
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total_vol = bid_vol + ask_vol
                wall_mid = (best_bid * ask_vol + best_ask * bid_vol) / total_vol

                self.history["ASH_COATED_OSMIUM"].append(wall_mid)
                if len(self.history["ASH_COATED_OSMIUM"]) > 100: self.history["ASH_COATED_OSMIUM"].pop(0)

                # 1. AR2 Prediction
                p = self.history["ASH_COATED_OSMIUM"]
                if len(p) >= 4:
                    x_t, x_t1, x_t2 = p[-1]-p[-2], p[-2]-p[-3], p[-3]-p[-4]
                    error = x_t - (self.phi1 * x_t1 + self.phi2 * x_t2)
                    norm = (x_t1**2 + x_t2**2) + 1e-6
                    self.phi1 = max(-1.0, min(1.0, self.phi1 + (AR2_LR * error * x_t1) / norm))
                    self.phi2 = max(-1.0, min(1.0, self.phi2 + (AR2_LR * error * x_t2) / norm))

                ar2_pred = self.phi1 * (p[-1]-p[-2]) + self.phi2 * (p[-2]-p[-3]) if len(p) >= 3 else 0.0

                # 2. Mean Reversion Anchor
                long_mean = sum(p[-LONG_WINDOW:]) / min(len(p), LONG_WINDOW)
                mr_term = (long_mean - wall_mid) * MR_STRENGTH
                
                w_ar, w_mr = (0.8, 0.2) if abs(ar2_pred) > 1.5 else (0.4, 0.6)
                fair_price = wall_mid + (w_ar * ar2_pred) + (w_mr * mr_term)

                # 3. Execution (OBI & Avellaneda-Stoikov)
                buy_cap = OSMIUM_LIMIT - current_pos
                sell_cap = -OSMIUM_LIMIT - current_pos
                market_spread = best_ask - best_bid

                obi = (bid_vol - ask_vol) / total_vol
                skew_intensity = (abs(current_pos) / OSMIUM_LIMIT) ** 1.5
                reservation_price = fair_price - (current_pos * OSMIUM_GAMMA * (1 + skew_intensity))

                obi_shift = obi * OBI_SENSITIVITY
                dynamic_spread = max(MIN_SPREAD, (market_spread / 2.0) + abs(obi))

                target_bid = reservation_price - dynamic_spread + obi_shift
                target_ask = reservation_price + dynamic_spread + obi_shift

                # Taker
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if ask_price < reservation_price - 0.5 and buy_cap > 0:
                        take_vol = min(abs(order_depth.sell_orders[ask_price]), buy_cap)
                        product_orders.append(Order(product, ask_price, take_vol))
                        buy_cap -= take_vol

                for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                    if bid_price > reservation_price + 0.5 and sell_cap < 0:
                        take_vol = max(-order_depth.buy_orders[bid_price], sell_cap)
                        product_orders.append(Order(product, bid_price, take_vol))
                        sell_cap -= take_vol

                # Maker
                pb = int(math.floor(min(target_bid, best_ask - 1)))
                pa = int(math.ceil(max(target_ask, best_bid + 1)))

                if pb >= pa: pb, pa = int(best_bid), int(best_ask)

                q_buy = min(buy_cap, max(15, int(buy_cap * 0.6)))
                q_sell = max(sell_cap, min(-15, int(sell_cap * 0.6)))

                if buy_cap > 0: product_orders.append(Order(product, pb, q_buy))
                if sell_cap < 0: product_orders.append(Order(product, pa, q_sell))

            if product_orders:
                all_orders[product] = product_orders

        # 4. SAVE STATE
        state_to_save = {
            'history': self.history,
            'bb_history': self.bb_history,
            'phi1': self.phi1,
            'phi2': self.phi2,
            'pepper_slope': self.pepper_slope
        }
        return all_orders, 0, json.dumps(state_to_save)