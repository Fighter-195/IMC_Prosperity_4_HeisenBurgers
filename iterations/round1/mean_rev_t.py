import json
from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict
import math

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
        for bot_name, profile in self.bot_profiles.items():
            if profile.max_trade_size > 30:
                return f"WHALE_DETECTED"
        return "NORMAL_NOISE"

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # 1. RESTORE STATE
        history = {"TOMATOES": []}
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                history = saved.get("history", {"TOMATOES": []})
                raw_profiles = saved.get('bot_profiles', {})
                for bot_name, data in raw_profiles.items():
                    prof = BotProfile()
                    prof.total_volume_bought = data.get('total_volume_bought', 0)
                    prof.total_volume_sold = data.get('total_volume_sold', 0)
                    prof.trade_count = data.get('trade_count', 0)
                    prof.avg_trade_size = data.get('avg_trade_size', 0.0)
                    prof.max_trade_size = data.get('max_trade_size', 0)
                    self.bot_profiles[bot_name] = prof
            except Exception:
                pass

        self.update_bot_profiles(state)
        market_regime = self.analyze_market_environment()

        for product in state.order_depths:
            orders: List[Order] = []
            order_depth = state.order_depths[product]
            limit = 80
            current_pos = state.position.get(product, 0)
            
            buy_capacity = limit - current_pos
            sell_capacity = -limit - current_pos

            # =================================================================
            # EMERALDS — The 1900 Baseline + Sniping
            # =================================================================
            if product == "EMERALDS":
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 9990
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10010

                # 1. THE SNIPER
                for ask_price, ask_vol in order_depth.sell_orders.items():
                    if ask_price <= 9998:  
                        take_vol = min(abs(ask_vol), buy_capacity)
                        if take_vol > 0:
                            orders.append(Order(product, ask_price, take_vol))
                            buy_capacity -= take_vol

                for bid_price, bid_vol in order_depth.buy_orders.items():
                    if bid_price >= 10002: 
                        take_vol = min(bid_vol, abs(sell_capacity))
                        if take_vol > 0:
                            orders.append(Order(product, bid_price, -take_vol))
                            sell_capacity += take_vol

                # 2. THE MAKER
                my_buy_price  = min(best_bid + 1, 9999)
                my_sell_price = max(best_ask - 1, 10001)

                if buy_capacity > 0:
                    orders.append(Order(product, my_buy_price, buy_capacity))
                if sell_capacity < 0:
                    orders.append(Order(product, my_sell_price, sell_capacity))

            # =================================================================
            # TOMATOES — Linear Regression of EWMA (Aggressive Trend Follower)
            # =================================================================
            elif product == "TOMATOES":
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
                
                if not best_bid or not best_ask: continue
                mid_price = (best_bid + best_ask) / 2.0
                
                # Update rolling history (15 ticks)
                history["TOMATOES"].append(mid_price)
                if len(history["TOMATOES"]) > 15:
                    history["TOMATOES"].pop(0)
                    
                if len(history["TOMATOES"]) == 15:
                    # 1. Calculate EWMA (Alpha = 0.4)
                    alpha = 0.4
                    ewma_hist = []
                    curr_ewma = history["TOMATOES"][0]
                    for p in history["TOMATOES"]:
                        curr_ewma = (alpha * p) + ((1 - alpha) * curr_ewma)
                        ewma_hist.append(curr_ewma)
                        
                    # 2. Linear Regression on the EWMA
                    N = len(ewma_hist)
                    x_mean = sum(range(N)) / N
                    y_mean = sum(ewma_hist) / N
                    
                    numerator = sum((i - x_mean) * (ewma_hist[i] - y_mean) for i in range(N))
                    denominator = sum((i - x_mean)**2 for i in range(N))
                    slope = numerator / denominator if denominator != 0 else 0
                    
                    # 3. Determine Ideal Taker Position
                    # Multiply slope by a scaling factor. If slope is +0.5, we want to be heavily long.
                    ideal_position = int(slope * 150) 
                    ideal_position = max(-limit, min(limit, ideal_position)) # Clamp to [-80, 80]
                    
                    # 4. Execute "All In" to reach Ideal Position
                    trade_needed = ideal_position - current_pos
                    
                    if trade_needed > 0:
                        # We need to get MORE LONG. Take liquidity from the Asks.
                        take_vol = min(trade_needed, buy_capacity)
                        if take_vol > 0:
                            orders.append(Order(product, best_ask, take_vol))
                            buy_capacity -= take_vol
                            
                    elif trade_needed < 0:
                        # We need to get MORE SHORT. Take liquidity from the Bids.
                        take_vol = max(trade_needed, sell_capacity)
                        if take_vol < 0:
                            orders.append(Order(product, best_bid, take_vol))
                            sell_capacity -= take_vol
                            
                    # 5. Passive Market Making with whatever capacity is left
                    # We quote 1 tick *behind* the spread so we don't accidentally close our trend position
                    if buy_capacity > 0:
                        orders.append(Order(product, best_bid - 1, buy_capacity))
                    if sell_capacity < 0:
                        orders.append(Order(product, best_ask + 1, sell_capacity))
                        
                else:
                    # Fallback while history array fills up (first 15 ticks)
                    if buy_capacity > 0: orders.append(Order(product, best_bid, buy_capacity))
                    if sell_capacity < 0: orders.append(Order(product, best_ask, sell_capacity))

            result[product] = orders

        # =====================================================================
        # STATE SAVING & LOGGING
        # =====================================================================
        serializable_profiles = {}
        for bot_name, profile in self.bot_profiles.items():
            serializable_profiles[bot_name] = {
                'total_volume_bought': profile.total_volume_bought,
                'total_volume_sold': profile.total_volume_sold,
                'trade_count': profile.trade_count,
                'avg_trade_size': profile.avg_trade_size,
                'max_trade_size': profile.max_trade_size,
            }
            
        state_to_save = {
            'history': history,
            'bot_profiles': serializable_profiles
        }
        
        return result, 0, json.dumps(state_to_save)