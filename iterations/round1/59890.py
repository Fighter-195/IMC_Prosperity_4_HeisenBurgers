from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple
import math
import jsonpickle

class Trader:
    def __init__(self):
        self.kf_x_tomatoes = None
        self.kf_P_tomatoes = 1.0  
        
        # UPDATE: Optimized based on observed tick-to-tick variance and autocorrelation
        # R is high to ignore bid-ask bounce, Q is low to track slow macro drift.
        self.kf_Q = 0.01  
        self.kf_R = 5.0 

    def bid(self):
        return 15

    # =========================================================================
    # PART 0: UTILITY
    # =========================================================================

    def compute_micro_price(self, order_depth: OrderDepth) -> float:
        best_bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders) if order_depth.sell_orders else None

        if best_bid is not None and best_ask is not None:
            v_bid = order_depth.buy_orders[best_bid]
            v_ask = abs(order_depth.sell_orders[best_ask])
            total_v = v_bid + v_ask
            if total_v > 0:
                return (best_ask * v_bid + best_bid * v_ask) / total_v

        if best_bid is not None:
            return float(best_bid)
        if best_ask is not None:
            return float(best_ask)
        return 0.0

    # =========================================================================
    # PART 1: ALPHA ENGINE — Kalman Filter State Estimation
    # =========================================================================

    def alpha_engine(self, state: TradingState) -> Dict[str, float]:
        fair_values = {}
        fair_values['EMERALDS'] = 10000.0

        if 'TOMATOES' in state.order_depths:
            micro_price = self.compute_micro_price(state.order_depths['TOMATOES'])
            
            if micro_price > 0:
                # Kalman Filter Initialization
                if self.kf_x_tomatoes is None:
                    self.kf_x_tomatoes = micro_price
                    self.kf_P_tomatoes = 1.0
                else:
                    # 1. Prediction Step
                    x_pred = self.kf_x_tomatoes
                    p_pred = self.kf_P_tomatoes + self.kf_Q
                    
                    # 2. Update Step
                    K = p_pred / (p_pred + self.kf_R)
                    self.kf_x_tomatoes = x_pred + K * (micro_price - x_pred)
                    self.kf_P_tomatoes = (1 - K) * p_pred

            if self.kf_x_tomatoes is not None:
                fair_values['TOMATOES'] = self.kf_x_tomatoes

        return fair_values

    # =========================================================================
    # PART 2: RISK ENGINE
    # =========================================================================

    def risk_engine(self) -> Dict[str, int]:
        return {
            'EMERALDS': 80,
            'TOMATOES': 80,
        }

    # =========================================================================
    # PART 3: INVENTORY ENGINE — Asymptotic Avellaneda-Stoikov
    # =========================================================================

    def inventory_engine(self, state: TradingState, fair_values: Dict[str, float]) -> Dict[str, float]:
        """
        Computes the Reservation Price using a non-linear exponential skew.
        As inventory approaches the hard limit (80), the skew multiplier accelerates,
        aggressively dumping toxic flow before a complete inventory lock occurs.
        """
        reservation_prices = {}
        
        # Base risk factors
        risk_factors = {
            'EMERALDS': 0.04, 
            'TOMATOES': 0.10  
        }

        for product, fv in fair_values.items():
            current_pos = state.position.get(product, 0)
            base_gamma = risk_factors.get(product, 0.1)
            limit = 80.0
            
            # Non-linear penalty: e^(|q| / limit). 
            # At pos=0, multiplier is 1x. At pos=80, multiplier is ~2.7x.
            penalty_multiplier = math.exp(abs(current_pos) / limit)
            
            skew = current_pos * base_gamma * penalty_multiplier
            reservation_prices[product] = fv - skew

        return reservation_prices

    # =========================================================================
    # PART 4: EXECUTION ENGINE — Split Logic for Stationary vs Drifting Assets
    # =========================================================================

    def execution_engine(
        self,
        state: TradingState,
        fair_values: Dict[str, float],
        reservation_prices: Dict[str, float],
        limits: Dict[str, int],
    ) -> Dict[str, List[Order]]:
        
        all_orders: Dict[str, List[Order]] = {}

        # Burn-in period: let the Kalman Filter for Tomatoes stabilize
        if state.timestamp < 5000: 
            return {}

        for product, fv in fair_values.items():
            if product not in state.order_depths:
                continue

            order_depth = state.order_depths[product]
            current_pos = state.position.get(product, 0)
            limit = limits.get(product, 80)
            product_orders: List[Order] = []

            # ------------------------------------------------------------------
            # STRATEGY A: EXACT RAINFOREST RESIN / EMERALDS STRATEGY
            # ------------------------------------------------------------------
            if product == 'EMERALDS':
                true_price = 10000.0
                buy_capacity = limit - current_pos      
                sell_capacity = -limit - current_pos 

                # Phase 1: Market Taking (Immediate Favorable Trades)
                if order_depth.sell_orders and buy_capacity > 0:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if ask_price >= true_price:
                            break  
                        available_vol = abs(order_depth.sell_orders[ask_price])
                        take_vol = min(available_vol, buy_capacity)
                        product_orders.append(Order(product, ask_price, take_vol))
                        buy_capacity -= take_vol

                if order_depth.buy_orders and sell_capacity < 0:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_price <= true_price:
                            break  
                        available_vol = order_depth.buy_orders[bid_price] 
                        take_vol = max(-available_vol, sell_capacity)       
                        product_orders.append(Order(product, bid_price, take_vol))
                        sell_capacity -= take_vol  

                # Phase 2: Inventory Skew Flattening OR Passive Pennying
                skew_threshold = 60
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 9995
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10005

                # Overbid/Undercut existing liquidity while maintaining positive edge (min 1 tick)
                passive_bid = min(best_bid + 1, 9999)
                passive_ask = max(best_ask - 1, 10001)

                if current_pos >= skew_threshold:
                    # Too long: flatten at exactly 10,000 to free up capacity
                    if sell_capacity < 0:
                        product_orders.append(Order(product, 10000, sell_capacity))
                    if buy_capacity > 0:
                        product_orders.append(Order(product, passive_bid, buy_capacity))

                elif current_pos <= -skew_threshold:
                    # Too short: flatten at exactly 10,000 to free up capacity
                    if buy_capacity > 0:
                        product_orders.append(Order(product, 10000, buy_capacity))
                    if sell_capacity < 0:
                        product_orders.append(Order(product, passive_ask, sell_capacity))

                else:
                    # Normal operations: maintain positive edge pennying
                    if buy_capacity > 0:
                        product_orders.append(Order(product, passive_bid, buy_capacity))
                    if sell_capacity < 0:
                        product_orders.append(Order(product, passive_ask, sell_capacity))


            # ------------------------------------------------------------------
            # STRATEGY B: TOMATOES (Drifting Asset - Kalman & Avellaneda)
            # ------------------------------------------------------------------
            elif product == 'TOMATOES':
                p_res = reservation_prices.get(product, fv)
                
                # Soft Limits (Technique from earlier)
                soft_limit = limit * 0.5 
                buy_capacity = limit - current_pos if current_pos < soft_limit else 0      
                sell_capacity = -limit - current_pos if current_pos > -soft_limit else 0     

                # Phase 1: Market Taking (Alpha Capture)
                if order_depth.sell_orders and buy_capacity > 0:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if ask_price >= (fv - 0.5): 
                            break  
                        available_vol = abs(order_depth.sell_orders[ask_price])
                        take_vol = min(available_vol, buy_capacity)
                        product_orders.append(Order(product, ask_price, take_vol))
                        buy_capacity -= take_vol

                if order_depth.buy_orders and sell_capacity < 0:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_price <= (fv + 0.5):
                            break  
                        available_vol = order_depth.buy_orders[bid_price] 
                        take_vol = max(-available_vol, sell_capacity)       
                        product_orders.append(Order(product, bid_price, take_vol))
                        sell_capacity -= take_vol  

                # Phase 2: Dynamic Pegging & OBI Check
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else math.floor(fv - 2)
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else math.ceil(fv + 2)

                vol_bid = order_depth.buy_orders.get(best_bid, 0)
                vol_ask = abs(order_depth.sell_orders.get(best_ask, 0))
                total_vol = vol_bid + vol_ask
                obi = (vol_bid - vol_ask) / total_vol if total_vol > 0 else 0.0

                heavy_buy_pressure = obi > 0.6
                heavy_sell_pressure = obi < -0.6

                passive_bid = min(math.floor(p_res), best_bid)
                passive_ask = max(math.ceil(p_res), best_ask)

                if buy_capacity > 0 and not heavy_sell_pressure:
                    product_orders.append(Order(product, passive_bid, buy_capacity))
                    
                if sell_capacity < 0 and not heavy_buy_pressure:
                    product_orders.append(Order(product, passive_ask, sell_capacity))

            if product_orders:
                all_orders[product] = product_orders

        return all_orders

    # =========================================================================
    # MAIN EVENT LOOP
    # =========================================================================

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        # Restore Kalman Filter state
        if state.traderData:
            try:
                saved = jsonpickle.decode(state.traderData)
                self.kf_x_tomatoes = saved.get('kf_x_tomatoes', None)
                self.kf_P_tomatoes = saved.get('kf_P_tomatoes', 1.0)
            except Exception:
                pass 

        fair_values = self.alpha_engine(state)
        limits = self.risk_engine()
        reservation_prices = self.inventory_engine(state, fair_values)
        final_orders = self.execution_engine(state, fair_values, reservation_prices, limits)
        conversions = 0

        # Persist Kalman Filter variables
        state_to_save = {
            'kf_x_tomatoes': self.kf_x_tomatoes,
            'kf_P_tomatoes': self.kf_P_tomatoes
        }
        traderData = jsonpickle.encode(state_to_save)
 
        return final_orders, conversions, traderData