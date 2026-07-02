from datamodel import OrderDepth, TradingState, Order, Trade
from typing import Dict, List, Tuple
import math
import json

# =========================================================================
# ROUND 1 SUBMISSION PARAMETERS (Avellaneda-Stoikov & Tranching)
# =========================================================================
HYPERPARAMETERS = {
    "PEPPER_ROOT_PANIC_THRESHOLD": 65,
    
    # 1. Validated Alpha Signals
    "OSMIUM_PHI1": -0.45,
    "OSMIUM_PHI2": 0.40,
    "OBI_IMPACT": 0.5,             # How much Order Book Imbalance shifts the fair price
    
    # 2. Avellaneda-Stoikov Risk Management
    "RISK_AVERSION": 0.015,        # Shifts Reservation Price based on inventory
    "BASE_HALF_SPREAD": 0.5,       # Minimum spread required to make a market
    "VOL_SPREAD_MULTIPLIER": 0.8,  # How much to widen spread during high volatility
    
    # 3. Inventory Sizing (No more All-In)
    "BASE_CLIP_SIZE": 10,          # Max size per order level (Tranching)
    "TAKER_CLIP_SIZE": 10,         # Max size to aggressively take from the book per tick
    
    # 4. Validated Bollinger Failsafe
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
        self.history: Dict[str, List[float]] = {"ASH_COATED_OSMIUM": [], "INTARIAN_PEPPER_ROOT": []}
        self.bb_history: List[float] = []   

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

    def analyze_market_environment(self) -> str:
        for _, profile in self.bot_profiles.items():
            if profile.max_trade_size > 30:
                return "WHALE_DETECTED"
        return "NORMAL_NOISE"

    def calculate_volatility(self, prices: List[float]) -> float:
        if len(prices) < 2: return 0.0
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        return math.sqrt(variance)

    # -------------------------------------------------------------------------
    # EXECUTION ENGINE 
    # -------------------------------------------------------------------------

    def execution_engine(self, state: TradingState, market_regime: str) -> Dict[str, List[Order]]:
        all_orders: Dict[str, List[Order]] = {}
        limits = {'INTARIAN_PEPPER_ROOT': 80, 'ASH_COATED_OSMIUM': 80}

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            current_pos = state.position.get(product, 0)
            limit = limits.get(product, 80)
            product_orders: List[Order] = []

            # Safe extraction of best bids/asks to prevent crashes on empty books
            best_bid_book = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask_book = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            # ── INTARIAN_PEPPER_ROOT: Steady State Strategy ────────────────────────────────────
            if product == 'INTARIAN_PEPPER_ROOT':
                buy_capacity = limit - current_pos
                sell_capacity = -limit - current_pos

                # Dynamically calculate the true price using a rolling average
                if best_bid_book is not None and best_ask_book is not None:
                    mid = (best_bid_book + best_ask_book) / 2.0
                    self.history["INTARIAN_PEPPER_ROOT"].append(mid)
                    if len(self.history["INTARIAN_PEPPER_ROOT"]) > 20:
                        self.history["INTARIAN_PEPPER_ROOT"].pop(0)
                
                if len(self.history["INTARIAN_PEPPER_ROOT"]) > 0:
                    true_price = sum(self.history["INTARIAN_PEPPER_ROOT"]) / len(self.history["INTARIAN_PEPPER_ROOT"])
                else:
                    true_price = mid if (best_bid_book is not None and best_ask_book is not None) else 10000.0

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

                # Safe Maker Quotes
                safe_best_bid = best_bid_book if best_bid_book is not None else math.floor(true_price) - 1
                safe_best_ask = best_ask_book if best_ask_book is not None else math.ceil(true_price) + 1

                passive_bid = min(safe_best_bid + 1, math.floor(true_price))
                passive_ask = max(safe_best_ask - 1, math.ceil(true_price))

                panic = HYPERPARAMETERS["PEPPER_ROOT_PANIC_THRESHOLD"]

                if current_pos >= panic:
                    if sell_capacity < 0: product_orders.append(Order(product, math.floor(true_price), sell_capacity))
                    if buy_capacity > 0:  product_orders.append(Order(product, passive_bid, buy_capacity))
                elif current_pos <= -panic:
                    if buy_capacity > 0:  product_orders.append(Order(product, math.ceil(true_price), buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))
                else:
                    if buy_capacity > 0:  product_orders.append(Order(product, passive_bid, buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))

            # ── ASH_COATED_OSMIUM: Avellaneda-Stoikov + Sizing + Validated Base ───────
            elif product == 'ASH_COATED_OSMIUM':
                buy_capacity = limit - current_pos
                sell_capacity = -limit - current_pos

                # 1. ADVANCED MICRO-PRICE: Order Book Imbalance (OBI)
                obi_shift = 0.0
                if best_bid_book is not None and best_ask_book is not None:
                    bid_vol = order_depth.buy_orders[best_bid_book]
                    ask_vol = abs(order_depth.sell_orders[best_ask_book])
                    total_vol = bid_vol + ask_vol
                    wall_mid = (best_bid_book + best_ask_book) / 2.0
                    
                    obi = (bid_vol - ask_vol) / total_vol
                    obi_shift = obi * HYPERPARAMETERS["OBI_IMPACT"]
                elif best_bid_book is not None:
                    wall_mid = float(best_bid_book)
                elif best_ask_book is not None:
                    wall_mid = float(best_ask_book)
                else:
                    continue # Skip tick if order book is totally empty

                self.history["ASH_COATED_OSMIUM"].append(wall_mid)
                if len(self.history["ASH_COATED_OSMIUM"]) > 10:
                    self.history["ASH_COATED_OSMIUM"].pop(0)

                # 2. ALPHA: AR(2) Prediction
                ar2_pred = 0.0
                if len(self.history["ASH_COATED_OSMIUM"]) >= 3:
                    p0 = self.history["ASH_COATED_OSMIUM"][-1]
                    p1 = self.history["ASH_COATED_OSMIUM"][-2]
                    p2 = self.history["ASH_COATED_OSMIUM"][-3]
                    
                    phi1 = HYPERPARAMETERS["OSMIUM_PHI1"]
                    phi2 = HYPERPARAMETERS["OSMIUM_PHI2"]
                    ar2_pred = phi1 * (p0 - p1) + phi2 * (p1 - p2)

                # 3. FAILSAFE: Bollinger Band
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

                # THE NEW FAIR PRICE
                fair_price = wall_mid + ar2_pred + bb_correction + obi_shift

                # 4. AVELLANEDA-STOIKOV INVENTORY MANAGEMENT
                # Calculate Reservation Price (Internal adjusted value based on risk)
                reservation_price = fair_price - (current_pos * HYPERPARAMETERS["RISK_AVERSION"])
                
                # Calculate Dynamic Spread (Widens when volatility increases)
                volatility = self.calculate_volatility(self.history["ASH_COATED_OSMIUM"])
                dynamic_half_spread = HYPERPARAMETERS["BASE_HALF_SPREAD"] + (volatility * HYPERPARAMETERS["VOL_SPREAD_MULTIPLIER"])

                if market_regime == "WHALE_DETECTED":
                    buy_capacity = min(buy_capacity, 10)
                    sell_capacity = max(sell_capacity, -10)
                    dynamic_half_spread += 1.0 # Widen spread manually against whales

                # --- TAKER EXECUTION (Sized) ---
                max_take = HYPERPARAMETERS["TAKER_CLIP_SIZE"]
                
                if order_depth.sell_orders and buy_capacity > 0:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if ask_price < fair_price:
                            take_vol = min(abs(order_depth.sell_orders[ask_price]), buy_capacity, max_take)
                            product_orders.append(Order(product, ask_price, take_vol))
                            buy_capacity -= take_vol
                            max_take -= take_vol

                max_take = HYPERPARAMETERS["TAKER_CLIP_SIZE"]
                if order_depth.buy_orders and sell_capacity < 0:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_price > fair_price:
                            take_vol = max(-order_depth.buy_orders[bid_price], sell_capacity, -max_take)
                            product_orders.append(Order(product, bid_price, take_vol))
                            sell_capacity -= take_vol
                            max_take += take_vol # take_vol is negative

                        
                # --- MAKER EXECUTION (Laddered/Tranched) ---
                ideal_bid = math.floor(reservation_price - dynamic_half_spread)
                ideal_ask = math.ceil(reservation_price + dynamic_half_spread)

                safe_best_bid = best_bid_book if best_bid_book is not None else ideal_bid - 1
                safe_best_ask = best_ask_book if best_ask_book is not None else ideal_ask + 1

                passive_bid = min(safe_best_bid + 1, ideal_bid)
                passive_ask = max(safe_best_ask - 1, ideal_ask)

                clip = HYPERPARAMETERS["BASE_CLIP_SIZE"]

                # Level 1: Primary aggressive quotes
                if buy_capacity > 0:
                    vol1 = min(buy_capacity, clip)
                    product_orders.append(Order(product, passive_bid, vol1))
                    buy_capacity -= vol1
                if sell_capacity < 0:
                    vol1 = max(sell_capacity, -clip)
                    product_orders.append(Order(product, passive_ask, vol1))
                    sell_capacity -= vol1

                # Level 2: Secondary defensive quotes (Laddering 1 tick further back)
                if buy_capacity > 0:
                    product_orders.append(Order(product, passive_bid - 1, buy_capacity))
                if sell_capacity < 0:
                    product_orders.append(Order(product, passive_ask + 1, sell_capacity))

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
                self.history = saved.get('history', {"ASH_COATED_OSMIUM": [], "INTARIAN_PEPPER_ROOT": []})
                self.bb_history = saved.get('bb_history', [])
                
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
        market_regime = self.analyze_market_environment()
        final_orders = self.execution_engine(state, market_regime)

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
        }

        log_payload = {
            "timestamp": state.timestamp, 
            "profiles": serializable_profiles
        }
        print(f"BOT_TRACKER_DATA::{json.dumps(log_payload)}")
        return final_orders, 0, json.dumps(state_to_save)