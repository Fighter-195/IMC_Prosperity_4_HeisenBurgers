import math
import json
import statistics
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple

class Trader:

    # -----------------------------
    # Black-Scholes Math (The Options Engine)
    # -----------------------------
    def norm_cdf(self, x: float) -> float:
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    def bs_price(self, S: float, K: float, T: float, sigma: float) -> float:
        if T <= 0 or S <= 0: return max(0.0, S - K)
        d1 = (math.log(S/K) + 0.5 * sigma**2 * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * self.norm_cdf(d1) - K * self.norm_cdf(d2)

    def bs_delta(self, S: float, K: float, T: float, sigma: float) -> float:
        if T <= 0 or S <= 0: return 1.0 if S > K else 0.0
        d1 = (math.log(S/K) + 0.5 * sigma**2 * T) / (sigma * math.sqrt(T))
        return self.norm_cdf(d1)

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        position = state.position

        # --- STATE PARSING (For Z-Scores & Momentum) ---
        try:
            state_data = json.loads(state.traderData)
        except Exception:
            state_data = {"hydro_mid": []}
            
        hydro_mid_hist = state_data.get("hydro_mid", [])

        def get_best_prices(order_depth: OrderDepth):
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
            return best_bid, best_ask

        def clip(volume, pos, limit):
            return int(max(-limit - pos, min(limit - pos, volume)))

        # Get underlying mid early for ITM/ATM pricing
        vev_mid = 5250.0
        if "VELVETFRUIT_EXTRACT" in state.order_depths:
            b, a = get_best_prices(state.order_depths["VELVETFRUIT_EXTRACT"])
            if b and a: vev_mid = (b + a) / 2.0

        T = max(0.0001, (5.0 - (state.timestamp / 1000000.0)) / 252.0)
        SIGMA = 0.18

        # -----------------------------
        # 0. BIDIRECTIONAL DELTA FIREWALL
        # -----------------------------
        net_delta = 0.0
        deltas = {}
        
        for strike in [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500]:
            d = self.bs_delta(vev_mid, strike, T, SIGMA)
            deltas[strike] = d
            net_delta += position.get(f"VEV_{strike}", 0) * d

        avail_long_delta = 200.0 - net_delta
        avail_short_delta = -200.0 - net_delta  

        # -----------------------------
        # 1. HYDROGEL_PACK (The Z-Score & Momentum Sauce)
        # -----------------------------
        product = "HYDROGEL_PACK"
        if product in state.order_depths:
            order_depth = state.order_depths[product]
            orders = []
            pos = position.get(product, 0)
            LIMIT = 200
            best_bid, best_ask = get_best_prices(order_depth)

            if best_bid is not None and best_ask is not None:
                mid_price = (best_bid + best_ask) / 2.0
                
                # Update Rolling History (Max 20 ticks)
                hydro_mid_hist.append(mid_price)
                if len(hydro_mid_hist) > 20: hydro_mid_hist.pop(0)

                # Statistical Calculations
                std_dev = statistics.stdev(hydro_mid_hist) if len(hydro_mid_hist) >= 2 else 2.0
                std_dev = max(1.0, std_dev) # Prevent division by zero
                
                z_score = (mid_price - 10000.0) / std_dev
                momentum = mid_price - hydro_mid_hist[-5] if len(hydro_mid_hist) >= 5 else 0.0

                # --- A. Aggressive Z-Score Taker (With Momentum Filter) ---
                # Z <= -2.0 means highly undervalued. Momentum >= -1.0 means it stopped crashing.
                if z_score <= -2.0 and momentum >= -1.0:
                    size = clip(LIMIT, pos, LIMIT)
                    if size > 0:
                        orders.append(Order(product, int(best_ask), size))
                        pos += size  

                # Z >= 2.0 means highly overvalued. Momentum <= 1.0 means it stopped surging.
                elif z_score >= 2.0 and momentum <= 1.0:
                    size = clip(-LIMIT, pos, LIMIT)
                    if size < 0:
                        orders.append(Order(product, int(best_bid), size))
                        pos += size

                # --- B. Passive Liquidity & Inventory Skewing ---
                my_bid = best_bid + 1
                my_ask = best_ask - 1

                # Skew quotes based on inventory (e.g. if pos=+100, we bid 4 ticks lower)
                skew = int(pos / 25)
                my_bid -= skew
                my_ask -= skew

                # Hard constraints to the mean
                my_bid = min(my_bid, 9999)
                my_ask = max(my_ask, 10001)

                if my_bid >= best_ask: my_bid = best_ask - 1
                if my_ask <= best_bid: my_ask = best_bid + 1

                if my_bid < my_ask:  
                    # Dynamic Sizing based on inventory stress
                    base_size = 25
                    buy_size = clip(int(base_size - (pos * 0.2)), pos, LIMIT)
                    sell_size = clip(int(-base_size - (pos * 0.2)), pos, LIMIT)

                    if buy_size > 0 and my_bid < 10000: 
                        orders.append(Order(product, int(my_bid), buy_size))
                    if sell_size < 0 and my_ask > 10000: 
                        orders.append(Order(product, int(my_ask), sell_size))
                        
            result[product] = orders

        # -----------------------------
        # 2. VELVETFRUIT_EXTRACT (The Delta Skew Hedge)
        # -----------------------------
        product = "VELVETFRUIT_EXTRACT"
        if product in state.order_depths:
            order_depth = state.order_depths[product]
            orders = []
            pos = position.get(product, 0)
            LIMIT = 200
            best_bid, best_ask = get_best_prices(order_depth)

            if best_bid and best_ask:
                target_pos = -int(round(net_delta))
                target_pos = max(-LIMIT, min(LIMIT, target_pos))
                diff = target_pos - pos

                bid_price = best_bid + 1
                ask_price = best_ask - 1
                if bid_price >= ask_price:
                    bid_price = best_bid
                    ask_price = best_ask

                base_size = 20
                bid_size = clip(int(base_size + max(0, diff)), pos, LIMIT)
                ask_size = clip(int(-base_size + min(0, diff)), pos, LIMIT)

                if bid_size > 0: orders.append(Order(product, int(bid_price), bid_size))
                if ask_size < 0: orders.append(Order(product, int(ask_price), ask_size))
            result[product] = orders

        # -----------------------------
        # 3. OPTIONS REGIMES
        # -----------------------------
        # --- Priority 1: Deep ITM Arb ---
        for strike in [4000, 4500]:
            product = f"VEV_{strike}"
            if product not in state.order_depths: continue
            pos = position.get(product, 0)
            LIMIT = 300
            best_bid, best_ask = get_best_prices(state.order_depths[product])

            fair = vev_mid - strike
            my_bid = math.floor(fair - 5)
            my_ask = math.ceil(fair + 5)

            if best_bid: my_bid = max(my_bid, best_bid + 1)
            if best_ask: my_ask = min(my_ask, best_ask - 1)
            if best_ask and my_bid >= best_ask: my_bid = best_ask - 1
            if best_bid and my_ask <= best_bid: my_ask = best_bid + 1
            if my_bid >= my_ask: my_bid = my_ask - 1

            contract_delta = deltas[strike]
            
            allowed_buy = max(0, int(avail_long_delta / contract_delta)) if contract_delta > 0 else LIMIT
            allowed_sell = min(0, int(avail_short_delta / contract_delta)) if contract_delta > 0 else -LIMIT
            
            buy_size = clip(min(20, allowed_buy), pos, LIMIT)
            sell_size = clip(max(-20, allowed_sell), pos, LIMIT)

            orders = []
            if buy_size > 0:
                orders.append(Order(product, int(my_bid), buy_size))
                avail_long_delta -= (buy_size * contract_delta)
            if sell_size < 0:
                orders.append(Order(product, int(my_ask), sell_size))
                avail_short_delta -= (sell_size * contract_delta)
            result[product] = orders

        # --- Priority 2: Mid OTM Premium Harvest ---
        for strike in [5300, 5400, 5500]:
            product = f"VEV_{strike}"
            if product not in state.order_depths: continue
            pos = position.get(product, 0)
            LIMIT = 300
            best_bid, best_ask = get_best_prices(state.order_depths[product])

            SHORT_CAP = -50
            if pos > SHORT_CAP:
                if best_ask is not None and best_bid is not None: price = max(best_ask - 1, best_bid + 1)
                elif best_bid is not None: price = best_bid + 2
                else: price = best_ask - 1 if best_ask else strike
                
                contract_delta = deltas[strike]
                allowed_sell = min(0, int(avail_short_delta / contract_delta)) if contract_delta > 0 else -LIMIT
                
                sell_size = clip(max(-25, allowed_sell), pos, LIMIT)
                if sell_size < 0: 
                    result[product] = [Order(product, int(price), sell_size)]
                    avail_short_delta -= (sell_size * contract_delta)

        # --- Priority 3: The ATM Boost Engine ---
        for strike in [5000, 5100, 5200]:
            product = f"VEV_{strike}"
            if product not in state.order_depths: continue
            pos = position.get(product, 0)
            LIMIT = 300
            best_bid, best_ask = get_best_prices(state.order_depths[product])

            fair = self.bs_price(vev_mid, strike, T, SIGMA)
            my_bid = math.floor(fair - 1.5)
            my_ask = math.ceil(fair + 1.5)
            
            if best_bid: my_bid = max(my_bid, best_bid + 1)
            if best_ask: my_ask = min(my_ask, best_ask - 1)
            if best_ask and my_bid >= best_ask: my_bid = best_ask - 1
            if best_bid and my_ask <= best_bid: my_ask = best_bid + 1
            if my_bid >= my_ask: my_bid = my_ask - 1

            contract_delta = deltas[strike]
            
            allowed_buy = max(0, int(avail_long_delta / contract_delta)) if contract_delta > 0 else LIMIT
            allowed_sell = min(0, int(avail_short_delta / contract_delta)) if contract_delta > 0 else -LIMIT

            buy_size = clip(min(15, allowed_buy), pos, LIMIT)
            sell_size = clip(max(-15, allowed_sell), pos, LIMIT)

            orders = []
            if buy_size > 0:
                orders.append(Order(product, int(my_bid), buy_size))
                avail_long_delta -= (buy_size * contract_delta)
            if sell_size < 0:
                orders.append(Order(product, int(my_ask), sell_size))
                avail_short_delta -= (sell_size * contract_delta)
            result[product] = orders

        # --- Priority 4: Deep OTM Free Money ---
        for strike in [6000, 6500]:
            product = f"VEV_{strike}"
            if product not in state.order_depths: continue
            pos = position.get(product, 0)
            LIMIT = 300
            orders = []
            
            sell_size = clip(-100, pos, LIMIT)
            if sell_size < 0: orders.append(Order(product, 1, sell_size))
            buy_size = clip(100, pos, LIMIT)
            if buy_size > 0: orders.append(Order(product, 0, buy_size))
            if orders: result[product] = orders

        # -----------------------------
        # 4. FAILSAFE: ORNAMENTAL_BIO_PODS
        # -----------------------------
        product = "ORNAMENTAL_BIO_PODS"
        if product in state.order_depths:
            pos = position.get(product, 0)
            if pos > 0:
                best_bid, _ = get_best_prices(state.order_depths[product])
                if best_bid: result[product] = [Order(product, int(best_bid), int(-pos))]

        # Save state data for next tick
        state_data["hydro_mid"] = hydro_mid_hist
        traderData = json.dumps(state_data)

        return result, conversions, traderData