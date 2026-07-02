from datamodel import OrderDepth, TradingState, Order, Trade
from typing import Dict, List, Tuple
import math
import json
HYPERPARAMETERS = {
    "EMERALDS_PANIC_THRESHOLD": 65,  
    "TOMATOES_PHI1": -0.35, 
    "TOMATOES_PHI2": 0.2,  
    "TOMATOES_SKEW_DIVISOR": 34.0,   
    "TOMATOES_SKEW_POWER": 2.5, 
}
# =========================================================================
# HYPERPARAMETER TUNING STUDIO
# =========================================================================


# =========================================================================
# DATA MODELS (Outside the Trader class)
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

    def update_bot_profiles(self, state: TradingState):
        for product, trades in state.market_trades.items():
            for trade in trades:
                if trade.buyer and trade.buyer != "SUBMISSION": 
                    if trade.buyer not in self.bot_profiles:
                        self.bot_profiles[trade.buyer] = BotProfile()
                    
                    profile = self.bot_profiles[trade.buyer]
                    profile.total_volume_bought += trade.quantity
                    profile.trade_count += 1
                    profile.last_buy_price = trade.price
                    profile.max_trade_size = max(profile.max_trade_size, trade.quantity)

                if trade.seller and trade.seller != "SUBMISSION":
                    if trade.seller not in self.bot_profiles:
                        self.bot_profiles[trade.seller] = BotProfile()
                    
                    profile = self.bot_profiles[trade.seller]
                    profile.total_volume_sold += trade.quantity
                    profile.trade_count += 1
                    profile.last_sell_price = trade.price
                    profile.max_trade_size = max(profile.max_trade_size, trade.quantity)
                    
    def analyze_market_environment(self) -> str:
        """Flags dangerous or highly profitable market conditions."""
        for bot_name, profile in self.bot_profiles.items():
            if profile.max_trade_size > 30:
                return f"WHALE_DETECTED"
        return "NORMAL_NOISE"

    # -------------------------------------------------------------------------
    # EXECUTION ENGINE 
    # -------------------------------------------------------------------------

    def execution_engine(self, state: TradingState, market_regime: str) -> Dict[str, List[Order]]:
        all_orders: Dict[str, List[Order]] = {}
        limits = {'EMERALDS': 80, 'TOMATOES': 80}

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            current_pos = state.position.get(product, 0)
            limit = limits.get(product, 80)
            product_orders: List[Order] = []

            # =================================================================
            # STRATEGY A: EMERALDS (Original Stationary Strategy)
            # =================================================================
            if product == 'EMERALDS':
                true_price = 10000.0
                buy_capacity = limit - current_pos      
                sell_capacity = -limit - current_pos 

                # 1. Market Taking
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

                # 2. Market Making (Original +/- 60 Escape Valve)
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 9995
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10005
                
                passive_bid = min(best_bid + 1, 9999)
                passive_ask = max(best_ask - 1, 10001)

                if current_pos >= 60:
                    if sell_capacity < 0: product_orders.append(Order(product, 10000, sell_capacity))
                    if buy_capacity > 0: product_orders.append(Order(product, passive_bid, buy_capacity))
                elif current_pos <= -60:
                    if buy_capacity > 0: product_orders.append(Order(product, 10000, buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))
                else:
                    if buy_capacity > 0: product_orders.append(Order(product, passive_bid, buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))

            # =================================================================
            # STRATEGY B: TOMATOES (AR(2) + Non-Linear Skew + All-In Execution)
            # =================================================================
            elif product == 'TOMATOES':
                buy_capacity = limit - current_pos      
                sell_capacity = -limit - current_pos 

                best_bid_book = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
                best_ask_book = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

                if best_bid_book and best_ask_book:
                    wall_mid = (best_bid_book + best_ask_book) / 2.0
                elif best_bid_book:
                    wall_mid = float(best_bid_book)
                elif best_ask_book:
                    wall_mid = float(best_ask_book)
                else:
                    continue

                # Update local history for AR(2)
                self.history["TOMATOES"].append(wall_mid)
                if len(self.history["TOMATOES"]) > 5:
                    self.history["TOMATOES"].pop(0)

                # 1. ALPHA GENERATION: AR(2) Momentum Approximation
# 1. ALPHA GENERATION: AR(2) Momentum Approximation
                fair_price = wall_mid
                if len(self.history["TOMATOES"]) >= 3:
                    p0 = self.history["TOMATOES"][-1]
                    p1 = self.history["TOMATOES"][-2]
                    p2 = self.history["TOMATOES"][-3]
                    
                    # --- REPLACE YOUR OLD PHI DEFINITIONS WITH THESE ---
                    phi1 = -0.35
                    phi2 = 0.3
                    
                    predicted_change = phi1 * (p0 - p1) + phi2 * (p1 - p2)
                    fair_price = wall_mid + predicted_change

                if market_regime == "WHALE_DETECTED":
                    buy_capacity = 0 

                # 2. Market Taking (Anchored to Predictive Fair Price)
                if order_depth.sell_orders and buy_capacity > 0:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if ask_price < fair_price:  
                            take_vol = min(abs(order_depth.sell_orders[ask_price]), buy_capacity)
                            product_orders.append(Order(product, ask_price, take_vol))
                            buy_capacity -= take_vol

                if order_depth.buy_orders and sell_capacity < 0:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_price > fair_price:  
                            take_vol = max(-order_depth.buy_orders[bid_price], sell_capacity)       
                            product_orders.append(Order(product, bid_price, take_vol))
                            sell_capacity -= take_vol  

                # 3. Market Making (Quadratic Skew + ALL-IN)
                # Formula: sign * (pos / 30)^2
# 3. Market Making (Non-Linear Skew)
                pos_sign = 1 if current_pos > 0 else -1
                skew_power = HYPERPARAMETERS["TOMATOES_SKEW_POWER"]
                
                # Apply the dynamic power from the sweep
                skew = int(((abs(current_pos) / 30.0) ** 3)) * pos_sign

                # Calculate base bounds using predictive fair price, constrained by the order book
                passive_bid = min(best_bid_book + 1, math.floor(fair_price)) - skew
                passive_ask = max(best_ask_book - 1, math.ceil(fair_price)) - skew

                # Execute All-In at the skewed, predictive price
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
        
        # 1. RESTORE STATE
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                raw_profiles = saved.get('bot_profiles', {})
                self.history = saved.get('history', {"TOMATOES": []})
                
                for bot_name, data in raw_profiles.items():
                    prof = BotProfile()
                    prof.total_volume_bought = data.get('total_volume_bought', 0)
                    prof.total_volume_sold = data.get('total_volume_sold', 0)
                    prof.trade_count = data.get('trade_count', 0)
                    prof.avg_trade_size = data.get('avg_trade_size', 0.0)
                    prof.max_trade_size = data.get('max_trade_size', 0)
                    prof.last_buy_price = data.get('last_buy_price', None)
                    prof.last_sell_price = data.get('last_sell_price', None)
                    
                    self.bot_profiles[bot_name] = prof
            except Exception:
                pass

        # 2. GATHER INTELLIGENCE 
        self.update_bot_profiles(state)
        market_regime = self.analyze_market_environment()

        # 3. EXECUTE TRADES 
        final_orders = self.execution_engine(state, market_regime)
        conversions = 0

        # 4. SAVE STATE 
        serializable_profiles = {}
        for bot_name, profile in self.bot_profiles.items():
            serializable_profiles[bot_name] = {
                'total_volume_bought': profile.total_volume_bought,
                'total_volume_sold': profile.total_volume_sold,
                'trade_count': profile.trade_count,
                'avg_trade_size': profile.avg_trade_size,
                'max_trade_size': profile.max_trade_size,
                'last_buy_price': profile.last_buy_price,
                'last_sell_price': profile.last_sell_price
            }
            
        state_to_save = {
            'bot_profiles': serializable_profiles,
            'history': self.history
        }
        
        traderData = json.dumps(state_to_save)

        # Tracker Output
        log_payload = {
            "timestamp": state.timestamp,
            "profiles": serializable_profiles
        }
        print(f"BOT_TRACKER_DATA::{json.dumps(log_payload)}")

        return final_orders, conversions, traderData