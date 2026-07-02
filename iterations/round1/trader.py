import json
from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict

class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # # Silently log trades to check for 'Olivia'
        # for product in state.market_trades:
        #     for trade in state.market_trades[product]:
        #         print(f"[{state.timestamp}] {product} Vol: {trade.quantity} @ {trade.price} | Buyer: {trade.buyer}, Seller: {trade.seller}")

        history = json.loads(state.traderData) if state.traderData else {"TOMATOES": []}

        for product in state.order_depths:
            orders: List[Order] = []
            order_depth = state.order_depths[product]

            # ======================
            # EMERALDS — The 1900 Baseline + Sniping
            # ======================
            if product == "EMERALDS":
                current_pos = state.position.get("EMERALDS", 0)
                buy_limit  =  80 - current_pos
                sell_limit = -80 - current_pos

                best_bid = max(order_depth.buy_orders.keys())  if order_depth.buy_orders  else 9990
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10010

                # 1. THE SNIPER
                for ask_price, ask_vol in order_depth.sell_orders.items():
                    if ask_price <= 9998:  
                        take_vol = min(abs(ask_vol), buy_limit)
                        if take_vol > 0:
                            orders.append(Order(product, ask_price, take_vol))
                            buy_limit -= take_vol

                for bid_price, bid_vol in order_depth.buy_orders.items():
                    if bid_price >= 10002: 
                        take_vol = min(bid_vol, abs(sell_limit))
                        if take_vol > 0:
                            orders.append(Order(product, bid_price, -take_vol))
                            sell_limit += take_vol

                # 2. THE MAKER
                my_buy_price  = min(best_bid + 1, 9999)
                my_sell_price = max(best_ask - 1, 10001)

                if buy_limit > 0:
                    orders.append(Order(product, my_buy_price, buy_limit))
                if sell_limit < 0:
                    orders.append(Order(product, my_sell_price, sell_limit))

            # ======================
            # TOMATOES — The Apex Hierarchy
            # ======================
            # ======================
            # TOMATOES — Adaptive Logic
            # ======================
            if product == "TOMATOES":
                if order_depth.buy_orders and order_depth.sell_orders:
                    best_bid = max(order_depth.buy_orders.keys())
                    best_ask = min(order_depth.sell_orders.keys())
                    mid_price = (best_bid + best_ask) / 2

                    # Update History for the eventual WMA/AR2 switch
                    bv = order_depth.buy_orders.get(best_bid, 0)
                    av = abs(order_depth.sell_orders.get(best_ask, 0))
                    total_vol = bv + av
                    micro_price = (best_bid * av + best_ask * bv) / total_vol if total_vol > 0 else mid_price
                    
                    history["TOMATOES"].append(micro_price)
                    if len(history["TOMATOES"]) > 40:
                        history["TOMATOES"].pop(0)

                    current_pos = state.position.get("TOMATOES", 0)
                    buy_limit  =  80 - current_pos
                    sell_limit = -80 - current_pos

                    # --- PHASE 1: THE WARM-UP (Ticks 1-39) ---
                    # Strategy: Pure Spread Sniping + Micro-Reversion
                    if len(history["TOMATOES"]) < 40:
                        # Use the immediate Micro-price as a temporary 'Fair' anchor
                        # We only take trades with a very clear 1.5+ tick edge
                        for ask_price, ask_vol in order_depth.sell_orders.items():
                            if ask_price <= micro_price - 1.5:
                                take_vol = min(abs(ask_vol), buy_limit)
                                if take_vol > 0:
                                    orders.append(Order(product, ask_price, take_vol))
                                    buy_limit -= take_vol
                        
                        for bid_price, bid_vol in order_depth.buy_orders.items():
                            if bid_price >= micro_price + 1.5:
                                take_vol = min(bid_vol, abs(sell_limit))
                                if take_vol > 0:
                                    orders.append(Order(product, bid_price, -take_vol))
                                    sell_limit += take_vol
                        
                        # Market Make tightly around the current best spread to catch early noise
                        if buy_limit > 0:
                            orders.append(Order(product, best_bid + (1 if current_pos < 0 else 0), min(20, buy_limit)))
                        if sell_limit < 0:
                            orders.append(Order(product, best_ask - (1 if current_pos > 0 else 0), max(-20, sell_limit)))

                    # --- PHASE 2: THE APEX (Tick 40+) ---
                    else:
                        # --- TIER 2: AVERAGES & VELOCITY ---
                        fast_hist = history["TOMATOES"][-15:]
                        weights = list(range(1, 16))
                        fast_wma = sum(p * w for p, w in zip(fast_hist, weights)) / sum(weights)
                        slow_sma = sum(history["TOMATOES"]) / 40
                        
                        velocity = history["TOMATOES"][-1] - history["TOMATOES"][-4]

                        # Risk Management
                        recent_prices = history["TOMATOES"][-10:]
                        true_volatility = max(recent_prices) - min(recent_prices)
                        spread = min(2 + int(true_volatility / 3), 5)
                        skew = int(current_pos / 30)

                        # The Ultimate Fair Price: WMA + a dash of velocity prediction
                        fair_price = fast_wma + (velocity * 0.2)

                        # --- TIER 3: THE SNIPER ---
                        for ask_price, ask_vol in order_depth.sell_orders.items():
                            if ask_price <= fair_price - 1.2: 
                                take_vol = min(abs(ask_vol), buy_limit)
                                if take_vol > 0:
                                    orders.append(Order(product, ask_price, take_vol))
                                    buy_limit -= take_vol 
                        
                        for bid_price, bid_vol in order_depth.buy_orders.items():
                            if bid_price >= fair_price + 1.2:
                                take_vol = min(bid_vol, abs(sell_limit))
                                if take_vol > 0:
                                    orders.append(Order(product, bid_price, -take_vol))
                                    sell_limit += take_vol 

                        # --- TIER 4: THE OVERRIDE (Mean Reversion vs Normal) ---
                        macro_dev = micro_price - slow_sma
                        
                        if macro_dev > 3.0:
                            # Reverting Down Override
                            my_sell_price = best_ask - 1 
                            my_buy_price = round(slow_sma) - spread - skew 
                        elif macro_dev < -3.0:
                            # Reverting Up Override
                            my_buy_price = best_bid + 1 
                            my_sell_price = round(slow_sma) + spread - skew 
                        else:
                            # Normal Market Making
                            my_buy_price  = round(fair_price) - spread - skew
                            my_sell_price = round(fair_price) + spread - skew

                        # Size Throttling
                        edge = abs(mid_price - fast_wma)
                        size = 8 if edge > 4 else 15 if edge > 2 else 25
                        if abs(current_pos) > 60: size = int(size * 0.5)

                        # --- EXECUTION ---
                        if buy_limit > 0:
                            orders.append(Order(product, my_buy_price, min(size, buy_limit)))
                        if sell_limit < 0:
                            orders.append(Order(product, my_sell_price, max(-size, sell_limit)))
                        pass

                       

            result[product] = orders

        return result, 0, json.dumps(history)