from datamodel import OrderDepth, TradingState, Order, Trade
from typing import Dict, List, Tuple
import math
import json

# =========================================================================
# FINAL SUBMISSION PARAMETERS (Dynamic Learner Edition)
# =========================================================================
HYPERPARAMETERS = {
    "EMERALDS_PANIC_THRESHOLD": 65,
    
    # These are now INITIAL seeds. The bot will evolve these live.
    "TOMATOES_INITIAL_PHI1": -0.35,
    "TOMATOES_INITIAL_PHI2": 0.35,
    
    # Online Machine Learning Params
    "AR2_LEARNING_RATE": 0.02,     # How fast the bot updates its brain
    "INV_RISK_AVERSION": 0.03,     # How heavily inventory shifts the fair price
    
    "TOMATOES_SKEW_DIVISOR": 34.0,
    "TOMATOES_SKEW_POWER": 2.2,
    
    # Bollinger Z-Score Failsafe
    "BB_ALPHA": 0.5,
    "BB_THRESH": 2.5,
    "BB_WINDOW": 20,
}

# =========================================================================
# DATA MODELS
# =========================================================================

class BotProfile:
    def __init__(self):
        self.total_volume_bought = 0
        self.total_volume_sold = 0
        self.trade_count = 0
        self.avg_trade_size = 0.0
        self.max_trade_size = 0
        self.last_buy_price = None
        self.last_sell_price = None

# =========================================================================
# THE TRADER BOT
# =========================================================================

class Trader:
    def __init__(self):
        self.bot_profiles: Dict[str, BotProfile] = {}
        self.history: Dict[str, List[float]] = {"TOMATOES": []}
        self.bb_history: List[float] = []
        
        # Dynamic Brain State
        self.phi1 = HYPERPARAMETERS["TOMATOES_INITIAL_PHI1"]
        self.phi2 = HYPERPARAMETERS["TOMATOES_INITIAL_PHI2"]

    def update_bot_profiles(self, state: TradingState):
        for product, trades in state.market_trades.items():
            for trade in trades:
                if trade.buyer and trade.buyer != "SUBMISSION":
                    if trade.buyer not in self.bot_profiles:
                        self.bot_profiles[trade.buyer] = BotProfile()
                    p = self.bot_profiles[trade.buyer]
                    p.total_volume_bought += trade.quantity
                    p.trade_count += 1
                    p.last_buy_price = trade.price
                    p.max_trade_size = max(p.max_trade_size, trade.quantity)
                if trade.seller and trade.seller != "SUBMISSION":
                    if trade.seller not in self.bot_profiles:
                        self.bot_profiles[trade.seller] = BotProfile()
                    p = self.bot_profiles[trade.seller]
                    p.total_volume_sold += trade.quantity
                    p.trade_count += 1
                    p.last_sell_price = trade.price
                    p.max_trade_size = max(p.max_trade_size, trade.quantity)

    # -------------------------------------------------------------------------
    # EXECUTION ENGINE 
    # -------------------------------------------------------------------------

    def execution_engine(self, state: TradingState) -> Dict[str, List[Order]]:
        all_orders: Dict[str, List[Order]] = {}
        limits = {'EMERALDS': 80, 'TOMATOES': 80}

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            current_pos = state.position.get(product, 0)
            limit = limits.get(product, 80)
            product_orders: List[Order] = []

            # ── EMERALDS: Spread-Aware Pennying ───────────────────────
            if product == 'EMERALDS':
                true_price = 10000.0
                buy_capacity = limit - current_pos
                sell_capacity = -limit - current_pos

                if order_depth.sell_orders and buy_capacity > 0:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if ask_price >= true_price: break
                        take_vol = min(abs(order_depth.sell_orders[ask_price]), buy_capacity)
                        product_orders.append(Order(product, ask_price, take_vol))
                        buy_capacity -= take_vol

                if order_depth.buy_orders and sell_capacity < 0:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_price <= true_price: break
                        take_vol = max(-order_depth.buy_orders[bid_price], sell_capacity)
                        product_orders.append(Order(product, bid_price, take_vol))
                        sell_capacity -= take_vol

                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 9995
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10005
                
                if best_ask - best_bid > 1:
                    passive_bid = min(best_bid + 1, 9999)
                    passive_ask = max(best_ask - 1, 10001)
                else:
                    passive_bid = min(best_bid, 9999)
                    passive_ask = max(best_ask, 10001)

                em_panic = HYPERPARAMETERS["EMERALDS_PANIC_THRESHOLD"]

                if current_pos >= em_panic:
                    if sell_capacity < 0: product_orders.append(Order(product, 10000, sell_capacity))
                    if buy_capacity > 0:  product_orders.append(Order(product, passive_bid, buy_capacity))
                elif current_pos <= -em_panic:
                    if buy_capacity > 0:  product_orders.append(Order(product, 10000, buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))
                else:
                    if buy_capacity > 0:  product_orders.append(Order(product, passive_bid, buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))

            # ── TOMATOES: Dynamic LMS AR(2) + Inventory Pricing ──────────────
            elif product == 'TOMATOES':
                buy_capacity = limit - current_pos
                sell_capacity = -limit - current_pos

                best_bid_book = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
                best_ask_book = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

                if best_bid_book and best_ask_book:
                    bid_vol = order_depth.buy_orders[best_bid_book]
                    ask_vol = abs(order_depth.sell_orders[best_ask_book])
                    total_vol = bid_vol + ask_vol
                    wall_mid = (best_bid_book * ask_vol + best_ask_book * bid_vol) / total_vol
                elif best_bid_book:
                    wall_mid = float(best_bid_book)
                elif best_ask_book:
                    wall_mid = float(best_ask_book)
                else:
                    continue

                self.history["TOMATOES"].append(wall_mid)
                if len(self.history["TOMATOES"]) > 10:
                    self.history["TOMATOES"].pop(0)

# --- 1. DYNAMIC PHI LEARNER (NLMS Update) ---
                if len(self.history["TOMATOES"]) >= 4:
                    p_t = self.history["TOMATOES"][-1]
                    p_t1 = self.history["TOMATOES"][-2]
                    p_t2 = self.history["TOMATOES"][-3]
                    p_t3 = self.history["TOMATOES"][-4]

                    # Actual observed changes
                    x_t = p_t - p_t1
                    x_t1 = p_t1 - p_t2
                    x_t2 = p_t2 - p_t3

                    # What we *would* have predicted for x_t
                    predicted_x_t = self.phi1 * x_t1 + self.phi2 * x_t2
                    error = x_t - predicted_x_t

                    # [THE FIX: Normalized LMS to prevent volatility explosions]
                    norm = (x_t1**2 + x_t2**2) + 1e-6 
                    learning_rate = HYPERPARAMETERS["AR2_LEARNING_RATE"]
                    
                    self.phi1 += (learning_rate * error * x_t1) / norm
                    self.phi2 += (learning_rate * error * x_t2) / norm

                    # Clamp weights to prevent runaway divergence
                    self.phi1 = max(-1.0, min(1.0, self.phi1))
                    self.phi2 = max(-1.0, min(1.0, self.phi2))

                # --- 2. ALPHA GENERATION ---
                ar2_pred = 0.0
                if len(self.history["TOMATOES"]) >= 3:
                    p0 = self.history["TOMATOES"][-1]
                    p1 = self.history["TOMATOES"][-2]
                    p2 = self.history["TOMATOES"][-3]
                    ar2_pred = self.phi1 * (p0 - p1) + self.phi2 * (p1 - p2)

                # Bollinger failsafe correction
                self.bb_history.append(wall_mid)
                if len(self.bb_history) > HYPERPARAMETERS["BB_WINDOW"]:
                    self.bb_history.pop(0)

                bb_correction = 0.0
                if len(self.bb_history) == HYPERPARAMETERS["BB_WINDOW"]:
                    n = len(self.bb_history)
                    mu = sum(self.bb_history) / n
                    var = sum((x - mu) ** 2 for x in self.bb_history) / n
                    sigma = var ** 0.5 if var > 0 else 0.001
                    z = (wall_mid - mu) / sigma
                    
                    if abs(z) > HYPERPARAMETERS["BB_THRESH"]:
                        bb_correction = -z * HYPERPARAMETERS["BB_ALPHA"]

                # --- 3. INVENTORY-AWARE PRICING ---
                inv_penalty = current_pos * HYPERPARAMETERS["INV_RISK_AVERSION"]
                fair_price = wall_mid + ar2_pred + bb_correction - inv_penalty

                # --- 4. TAKER MARGINS (THE BLEED FIX) ---
                # The bot must demand a premium before crossing the spread
                base_margin = 1.0 # Demand at least 1 tick of edge
                inv_factor = 0.015 # Get more aggressive taking if inventory is maxed
                
                buy_margin = base_margin + (max(0, current_pos) * inv_factor)
                sell_margin = base_margin + (max(0, -current_pos) * inv_factor)

                # Taker Logic (Now protected by margins)
                if order_depth.sell_orders and buy_capacity > 0:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if ask_price <= fair_price - buy_margin:
                            take_vol = min(abs(order_depth.sell_orders[ask_price]), buy_capacity)
                            product_orders.append(Order(product, ask_price, take_vol))
                            buy_capacity -= take_vol

                if order_depth.buy_orders and sell_capacity < 0:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_price >= fair_price + sell_margin:
                            take_vol = max(-order_depth.buy_orders[bid_price], sell_capacity)
                            product_orders.append(Order(product, bid_price, take_vol))
                            sell_capacity -= take_vol

                # --- 5. MAKER LOGIC --- 
                pos_sign = 1 if current_pos > 0 else -1
                skew_power = HYPERPARAMETERS["TOMATOES_SKEW_POWER"]
                skew_divisor = HYPERPARAMETERS["TOMATOES_SKEW_DIVISOR"]
                
                skew = int(((abs(current_pos) / skew_divisor) ** skew_power)) * pos_sign

                # [SILENT CRASH FIX: Default to fair_price if the book is completely empty]
                safe_best_bid = best_bid_book if best_bid_book is not None else int(fair_price) - 1
                safe_best_ask = best_ask_book if best_ask_book is not None else int(fair_price) + 1

                passive_bid = min(safe_best_bid + 1, math.floor(fair_price)) - skew
                passive_ask = max(safe_best_ask - 1, math.ceil(fair_price)) - skew

                if buy_capacity > 0:
                    product_orders.append(Order(product, passive_bid, buy_capacity))
                if sell_capacity < 0:
                    product_orders.append(Order(product, passive_ask, sell_capacity))

            if product_orders:
                all_orders[product] = product_orders

        return all_orders

    # -------------------------------------------------------------------------
    # MAIN EVENT LOOP
    # -------------------------------------------------------------------------

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                raw_profiles = saved.get('bot_profiles', {})
                self.history = saved.get('history', {"TOMATOES": []})
                self.bb_history = saved.get('bb_history', [])
                
                # Restore the dynamic brains!
                self.phi1 = saved.get('phi1', self.phi1)
                self.phi2 = saved.get('phi2', self.phi2)
                
                for bot_name, data in raw_profiles.items():
                    prof = BotProfile()
                    prof.total_volume_bought = data.get('total_volume_bought', 0)
                    prof.total_volume_sold   = data.get('total_volume_sold', 0)
                    prof.trade_count         = data.get('trade_count', 0)
                    prof.avg_trade_size      = data.get('avg_trade_size', 0.0)
                    prof.max_trade_size      = data.get('max_trade_size', 0)
                    prof.last_buy_price      = data.get('last_buy_price', None)
                    prof.last_sell_price     = data.get('last_sell_price', None)
                    self.bot_profiles[bot_name] = prof
            except Exception:
                pass

        self.update_bot_profiles(state)
        final_orders = self.execution_engine(state)

        serializable_profiles = {}
        for bot_name, profile in self.bot_profiles.items():
            serializable_profiles[bot_name] = {
                'total_volume_bought': profile.total_volume_bought,
                'total_volume_sold':   profile.total_volume_sold,
                'trade_count':         profile.trade_count,
                'avg_trade_size':      profile.avg_trade_size,
                'max_trade_size':      profile.max_trade_size,
                'last_buy_price':      profile.last_buy_price,
                'last_sell_price':     profile.last_sell_price,
            }

        state_to_save = {
            'bot_profiles': serializable_profiles,
            'history':      self.history,
            'bb_history':   self.bb_history,
            'phi1':         self.phi1,
            'phi2':         self.phi2
        }

        # Enhanced tracker output to monitor brain evolution
        log_payload = {
            "timestamp": state.timestamp, 
            "phi1": self.phi1,
            "phi2": self.phi2,
            "profiles": serializable_profiles
        }
        print(f"BOT_TRACKER_DATA::{json.dumps(log_payload)}")
        
        return final_orders, 0, json.dumps(state_to_save)