from datamodel import OrderDepth, TradingState, Order, Trade
from typing import Dict, List, Tuple
import math
import json

# =========================================================================
# FINAL SUBMISSION PARAMETERS (Avellaneda-Stoikov & Tranching)
# =========================================================================
HYPERPARAMETERS = {
    "EMERALDS_PANIC_THRESHOLD": 65,
    
    # 1. Validated Alpha Signals
    "TOMATOES_PHI1": -0.45,
    "TOMATOES_PHI2": -0.226,       # Data-fitted (lag-2 return autocorr=-0.009, not +0.40; +0.40 gives 14% higher MAE)
    "OBI_IMPACT": 0.5,             # How much Order Book Imbalance shifts the fair price
    
    # 2. Avellaneda-Stoikov Risk Management
    "RISK_AVERSION": 0.07,         # Shifts Reservation Price (0.015=1.2 ticks at pos=80, too weak; 0.07=5.6 ticks, meaningful)
    "BASE_HALF_SPREAD": 0.5,       # Minimum spread required to make a market
    "VOL_SPREAD_MULTIPLIER": 0.8,  # How much to widen spread during high volatility
    
    # 3. Inventory Sizing (No more All-In)
    "BASE_CLIP_SIZE": 80,          # Full capacity at level 1 (clip=10 sent 70 units to Level 2 at market bid — never fills)
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
        self.history: Dict[str, List[float]] = {"TOMATOES": []}
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
        limits = {'EMERALDS': 80, 'TOMATOES': 80}

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            current_pos = state.position.get(product, 0)
            limit = limits.get(product, 80)
            product_orders: List[Order] = []

            # ── EMERALDS: Intact Strategy ────────────────────────────────────
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
                passive_bid = min(best_bid + 1, 9999)
                passive_ask = max(best_ask - 1, 10001)

                if current_pos >= 60:
                    if sell_capacity < 0: product_orders.append(Order(product, 10000, sell_capacity))
                    if buy_capacity > 0:  product_orders.append(Order(product, passive_bid, buy_capacity))
                elif current_pos <= -60:
                    if buy_capacity > 0:  product_orders.append(Order(product, 10000, buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))
                else:
                    if buy_capacity > 0:  product_orders.append(Order(product, passive_bid, buy_capacity))
                    if sell_capacity < 0: product_orders.append(Order(product, passive_ask, sell_capacity))

            # ── TOMATOES: Avellaneda-Stoikov + Sizing + Validated Base ───────
            elif product == 'TOMATOES':
                buy_capacity = limit - current_pos
                sell_capacity = -limit - current_pos

                best_bid_book = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
                best_ask_book = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

                # 1. ADVANCED MICRO-PRICE: Order Book Imbalance (OBI)
                obi_shift = 0.0
                if best_bid_book and best_ask_book:
                    bid_vol = order_depth.buy_orders[best_bid_book]
                    ask_vol = abs(order_depth.sell_orders[best_ask_book])
                    total_vol = bid_vol + ask_vol
                    wall_mid = (best_bid_book + best_ask_book) / 2.0
                    
                    obi = (bid_vol - ask_vol) / total_vol
                    obi_shift = obi * HYPERPARAMETERS["OBI_IMPACT"]
                elif best_bid_book:
                    wall_mid = float(best_bid_book)
                elif best_ask_book:
                    wall_mid = float(best_ask_book)
                else:
                    continue

                self.history["TOMATOES"].append(wall_mid)
                if len(self.history["TOMATOES"]) > 10:
                    self.history["TOMATOES"].pop(0)

                # 2. ALPHA: AR(2) Prediction
                ar2_pred = 0.0
                if len(self.history["TOMATOES"]) >= 3:
                    p0 = self.history["TOMATOES"][-1]
                    p1 = self.history["TOMATOES"][-2]
                    p2 = self.history["TOMATOES"][-3]
                    
                    phi1 = HYPERPARAMETERS["TOMATOES_PHI1"]
                    phi2 = HYPERPARAMETERS["TOMATOES_PHI2"]
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
                volatility = self.calculate_volatility(self.history["TOMATOES"])
                dynamic_half_spread = HYPERPARAMETERS["BASE_HALF_SPREAD"] + (volatility * HYPERPARAMETERS["VOL_SPREAD_MULTIPLIER"])

                if market_regime == "WHALE_DETECTED":
                    buy_capacity = min(buy_capacity, 10)
                    sell_capacity = max(sell_capacity, -10)
                    dynamic_half_spread += 1.0 # Widen spread manually against whales

                # --- PANIC UNWIND (pos ≥ 60 or ≤ -60) ---
                # Original "taker" panic (bid>fair-1.5) never fires: bid=mid-6.5,
                # fair≈mid, so -6.5 > -1.5 is structurally impossible (13-tick spread).
                # Fix: post AT the best quote to guarantee a fill this tick.
                PANIC_THRESH = 60
                if current_pos >= PANIC_THRESH:
                    buy_capacity = 0
                    if sell_capacity < 0:
                        dump = max(sell_capacity, -80)
                        product_orders.append(Order(product, best_bid_book, dump))
                        sell_capacity -= dump
                elif current_pos <= -PANIC_THRESH:
                    sell_capacity = 0
                    if buy_capacity > 0:
                        cover = min(buy_capacity, 80)
                        product_orders.append(Order(product, best_ask_book, cover))
                        buy_capacity -= cover

                # --- TAKER EXECUTION (Sized) ---
                # Instead of taking up to 80, we limit our taker size per tick to avoid slippage
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
                # Reservation price controls placement directly (no min/max book constraint).
                # Previous: min(bid+1, floor(res-dhs)) → bid+1=mid-5.5 always won,
                # making the entire A-S reservation math irrelevant.
                passive_bid = math.floor(reservation_price - dynamic_half_spread)
                passive_ask = math.ceil(reservation_price + dynamic_half_spread)

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
                self.history = saved.get('history', {"TOMATOES": []})
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