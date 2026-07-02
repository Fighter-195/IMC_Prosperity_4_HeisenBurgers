import math
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple, Any

class Trader:

    # -----------------------------
    # Black-Scholes Math (From Kill_Me.py for the ATM Boost)
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
        traderData = "R3_TRUE_FUSION"
        position = state.position

        # -----------------------------
        # Helper functions
        # -----------------------------
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

        # Time To Expiry & Volatility
        T = max(0.0001, (5.0 - (state.timestamp / 1000000.0)) / 252.0)
        SIGMA = 0.18

        # -----------------------------
        # 0. Compute NET DELTA (FIREWALL) early
        # Now dynamically calculates Delta for ALL options using BS
        # -----------------------------
        net_delta = 0.0
        for strike in [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500]:
            prod = f"VEV_{strike}"
            pos_opt = position.get(prod, 0)
            if pos_opt != 0:
                net_delta += pos_opt * self.bs_delta(vev_mid, strike, T, SIGMA)

        max_itm_capacity = max(0.0, 200.0 - net_delta)

        # -----------------------------
        # 1. HYDROGEL_PACK (Exact 12k Logic)
        # -----------------------------
        product = "HYDROGEL_PACK"
        if product in state.order_depths:
            order_depth = state.order_depths[product]
            orders = []
            pos = position.get(product, 0)
            LIMIT = 200

            best_bid, best_ask = get_best_prices(order_depth)

            if best_ask is not None and best_ask <= 9998:
                size = clip(LIMIT, pos, LIMIT)
                if size > 0:
                    orders.append(Order(product, int(best_ask), size))
                    pos += size  
            
            if best_bid is not None and best_bid >= 10002:
                size = clip(-LIMIT, pos, LIMIT)
                if size < 0:
                    orders.append(Order(product, int(best_bid), size))
                    pos += size

            if best_bid is not None and best_ask is not None:
                my_bid = min(best_bid + 1, 9999)
                my_ask = max(best_ask - 1, 10001)

                if my_bid >= best_ask: my_bid = best_ask - 1
                if my_ask <= best_bid: my_ask = best_bid + 1

                if my_bid < my_ask:  
                    buy_size = clip(25, pos, LIMIT)
                    sell_size = clip(-25, pos, LIMIT)

                    if buy_size > 0 and my_bid < 10000:
                        orders.append(Order(product, int(my_bid), buy_size))
                    if sell_size < 0 and my_ask > 10000:
                        orders.append(Order(product, int(my_ask), sell_size))

            result[product] = orders

        # -----------------------------
        # 2. VELVETFRUIT_EXTRACT (Exact 12k Logic)
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
        # 3. OPTIONS HANDLING
        # -----------------------------
        option_strikes = [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500]

        for strike in option_strikes:
            product = f"VEV_{strike}"
            if product not in state.order_depths: continue
            
            order_depth = state.order_depths[product]
            orders = []
            pos = position.get(product, 0)
            LIMIT = 300
            best_bid, best_ask = get_best_prices(order_depth)

            # -------------------------
            # Regime 1: Deep OTM (Exact 12k Logic)
            # -------------------------
            if strike >= 6000:
                sell_size = clip(-100, pos, LIMIT)
                if sell_size < 0: orders.append(Order(product, 1, sell_size))
                buy_size = clip(100, pos, LIMIT)
                if buy_size > 0: orders.append(Order(product, 0, buy_size))

            # -------------------------
            # Regime 2: Mid OTM (Exact 12k Logic)
            # -------------------------
            elif strike in [5300, 5400, 5500]:
                SHORT_CAP = -50
                if pos > SHORT_CAP:
                    if best_ask is not None and best_bid is not None:
                        price = max(best_ask - 1, best_bid + 1)
                    elif best_bid is not None:
                        price = best_bid + 2
                    else:
                        price = best_ask - 1 if best_ask else strike
                    
                    size = clip(-25, pos, LIMIT)
                    if size < 0: orders.append(Order(product, int(price), size))

            # -------------------------
            # Regime 3: Deep ITM (Exact 12k Logic)
            # -------------------------
            elif strike in [4000, 4500] and vev_mid is not None:
                fair = vev_mid - strike
                my_bid = math.floor(fair - 5)
                my_ask = math.ceil(fair + 5)

                if best_bid: my_bid = max(my_bid, best_bid + 1)
                if best_ask: my_ask = min(my_ask, best_ask - 1)

                if best_ask and my_bid >= best_ask: my_bid = best_ask - 1
                if best_bid and my_ask <= best_bid: my_ask = best_bid + 1
                
                if my_bid >= my_ask:
                    my_bid = best_bid if best_bid else my_bid
                    my_ask = best_ask if best_ask else my_ask

                if max_itm_capacity > 0:
                    buy_size = min(20, int(max_itm_capacity))
                    buy_size = clip(buy_size, pos, LIMIT)
                    if buy_size > 0:
                        orders.append(Order(product, int(my_bid), buy_size))
                        max_itm_capacity -= buy_size 

                sell_size = clip(-20, pos, LIMIT)
                if sell_size < 0:
                    orders.append(Order(product, int(my_ask), sell_size))

            # -------------------------
            # NEW Regime 4: The 19k ATM Boost
            # -------------------------
            elif strike in [5000, 5100, 5200] and vev_mid is not None:
                fair = self.bs_price(vev_mid, strike, T, SIGMA)
                contract_delta = self.bs_delta(vev_mid, strike, T, SIGMA)
                
                my_bid = math.floor(fair - 1.5)
                my_ask = math.ceil(fair + 1.5)
                
                if best_bid: my_bid = max(my_bid, best_bid + 1)
                if best_ask: my_ask = min(my_ask, best_ask - 1)
                
                if best_ask and my_bid >= best_ask: my_bid = best_ask - 1
                if best_bid and my_ask <= best_bid: my_ask = best_bid + 1
                if my_bid >= my_ask:
                    my_bid = best_bid if best_bid else my_bid
                    my_ask = best_ask if best_ask else my_ask

                # Protected by your Delta Firewall
                if max_itm_capacity > 0 and contract_delta > 0:
                    allowed_contracts = int(max_itm_capacity / contract_delta)
                    buy_size = min(15, allowed_contracts) # Throttled slightly for safety
                    buy_size = clip(buy_size, pos, LIMIT)
                    if buy_size > 0:
                        orders.append(Order(product, int(my_bid), buy_size))
                        max_itm_capacity -= (buy_size * contract_delta)
                
                sell_size = clip(-15, pos, LIMIT)
                if sell_size < 0:
                    orders.append(Order(product, int(my_ask), sell_size))

            result[product] = orders

        # -----------------------------
        # 4. FAILSAFE: ORNAMENTAL_BIO_PODS
        # -----------------------------
        product = "ORNAMENTAL_BIO_PODS"
        if product in state.order_depths:
            pos = position.get(product, 0)
            if pos > 0:
                best_bid, _ = get_best_prices(state.order_depths[product])
                if best_bid: result[product] = [Order(product, int(best_bid), int(-pos))]

        return result, conversions, traderData