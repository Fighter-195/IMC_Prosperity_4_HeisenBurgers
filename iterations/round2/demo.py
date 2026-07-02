from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
import math

# =========================================================================
# PARAMETERS
# =========================================================================
PEPPER_LIMIT = 80
OSMIUM_LIMIT = 80
FV = 10000  # The Absolute Peg for Osmium

# WINDOWS
OSMIUM_WINDOW = 20

class Trader:
    
    # =========================================================================
    # ROUND 2: MARKET ACCESS FEE BID (4100 XIRECs)
    # =========================================================================
    def bid(self) -> int:
        return 4100

    def __init__(self):
        self.history = {
            "ASH_COATED_OSMIUM": []
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

        if "ASH_COATED_OSMIUM" not in self.history: 
            self.history["ASH_COATED_OSMIUM"] = []

        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():
            
            # Failsafe against empty orderbook data ticks (prevents crashes)
            if not order_depth.buy_orders and not order_depth.sell_orders:
                continue

            current_pos = state.position.get(product, 0)
            orders: List[Order] = []

            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
            
            if best_bid is None or best_ask is None:
                continue

            # ============================================================
            # 🌶️ INTARIAN PEPPER ROOT: PURE BUY & HOLD 
            # ============================================================
            if product == 'INTARIAN_PEPPER_ROOT':
                buy_capacity = PEPPER_LIMIT - current_pos
                
                if buy_capacity > 0:
                    vol_at_ask = abs(order_depth.sell_orders[best_ask])
                    take_vol = min(buy_capacity, vol_at_ask)
                    if take_vol > 0:
                        orders.append(Order(product, best_ask, take_vol))

                if orders:
                    result[product] = orders
                
                continue

            # ============================================================
            # 🔥 OSMIUM: MARKET MAKING + BUY LOW/SELL HIGH 
            # ============================================================
            if product == "ASH_COATED_OSMIUM":
                buy_capacity = OSMIUM_LIMIT - current_pos
                sell_capacity = -OSMIUM_LIMIT - current_pos

                # 1. Calculate Mid Price and Track History
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = abs(order_depth.sell_orders[best_ask])
                total = bid_vol + ask_vol
                mid = (best_bid * ask_vol + best_ask * bid_vol) / total
                
                hist = self.history["ASH_COATED_OSMIUM"]
                hist.append(mid)
                if len(hist) > OSMIUM_WINDOW: 
                    hist.pop(0)

                # Wait until we have enough data to calculate a stable SMA
                if len(hist) < 5:
                    continue

                # 2. SMA & Standard Deviation (Noise) Calculation
                sma = sum(hist) / len(hist)
                variance = sum((x - sma)**2 for x in hist) / len(hist)
                std_dev = math.sqrt(variance)
                
                # Protect against 0 variance if price stalls
                std_dev = max(1.5, std_dev)

                # 3. Dynamic Inventory Skew
                # The more we hold, the more aggressively we shift our fair price to offload
                urgency = current_pos / OSMIUM_LIMIT
                inventory_skew = urgency * 2.5  # Skew up to 2.5 ticks away from SMA
                
                fair_price = sma - inventory_skew

                # 4. TAKER LOGIC (BUY LOW / SELL HIGH)
                # We only take liquidity if the price has violently deviated from our fair price
                take_bid_threshold = fair_price - (std_dev * 0.8)
                take_ask_threshold = fair_price + (std_dev * 0.8)

                if order_depth.sell_orders:
                    for ask_price, vol in sorted(order_depth.sell_orders.items()):
                        # BUY LOW
                        if ask_price <= take_bid_threshold and buy_capacity > 0:
                            trade_vol = min(abs(vol), buy_capacity)
                            if trade_vol > 0:
                                orders.append(Order(product, ask_price, trade_vol))
                                buy_capacity -= trade_vol

                if order_depth.buy_orders:
                    for bid_price, vol in sorted(order_depth.buy_orders.items(), reverse=True):
                        # SELL HIGH
                        if bid_price >= take_ask_threshold and sell_capacity < 0:
                            trade_vol = max(-abs(vol), sell_capacity)
                            if trade_vol < 0:
                                orders.append(Order(product, bid_price, trade_vol))
                                sell_capacity -= trade_vol

                # 5. MAKER LOGIC (MARKET MAKING / SPREAD FARMING)
                # Provide liquidity exactly around our inventory-adjusted fair price
                ideal_bid = math.floor(fair_price - 1.0)
                ideal_ask = math.ceil(fair_price + 1.0)

                # Ensure we don't accidentally cross our own book or overpay
                pb = min(ideal_bid, best_ask - 1) if best_ask else ideal_bid
                pa = max(ideal_ask, best_bid + 1) if best_bid else ideal_ask

                if best_bid and best_ask and pb >= pa:
                    pb = best_bid
                    pa = best_ask

                # The Elastic 10k Failsafe
                # As we accumulate inventory, slowly shift the hard FV clamp
                peg_stretch = int(abs(current_pos) / 20)
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

                # Volume Laddering execution
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

        # SAVE STATE
        state_to_save = {
            'history': self.history
        }
        
        return result, 0, json.dumps(state_to_save)