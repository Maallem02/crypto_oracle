import pandas as pd
import numpy as np

def get_premium_discount(swing_low: float, swing_high: float, current_price: float) -> dict:
    """
    Calcule les zones Premium/Discount basées sur le dernier swing
    
    Discount Zone : 50% - 100% du range (zone d'achat)
    Premium Zone  : 0%  - 50%  du range (zone de vente)
    Equilibrium   : 50% exact
    """
    if swing_high == swing_low:
        return {"zone": "neutral", "position_pct": 50.0}

    diff         = swing_high - swing_low
    equilibrium  = swing_low + diff * 0.5

    # Position du prix dans le range (0% = swing low, 100% = swing high)
    position_pct = ((current_price - swing_low) / diff) * 100
    position_pct = max(0, min(100, position_pct))

    # Zones
    if position_pct <= 25:
        zone = "deep_discount"    # meilleure zone BUY
    elif position_pct <= 50:
        zone = "discount"         # zone BUY
    elif position_pct <= 75:
        zone = "premium"          # zone SELL
    else:
        zone = "deep_premium"     # meilleure zone SELL

    # Est-ce que le prix est dans la bonne zone pour trader ?
    is_buy_zone  = position_pct <= 50
    is_sell_zone = position_pct >= 50

    return {
        "zone":          zone,
        "position_pct":  round(position_pct, 2),
        "equilibrium":   round(equilibrium, 5),
        "swing_high":    round(swing_high, 5),
        "swing_low":     round(swing_low, 5),
        "is_buy_zone":   is_buy_zone,
        "is_sell_zone":  is_sell_zone,
        "discount_top":  round(equilibrium, 5),
        "premium_bottom": round(equilibrium, 5),
    }

def get_fib_zone(swing_low: float, swing_high: float, current_price: float) -> dict:
    """
    Fibonacci-based premium/discount zones — more granular than the flat
    25/50/75 split used by get_premium_discount(). Same range (swing_low →
    swing_high), but boundaries follow standard Fib retracement ratios:

      0%    - 38.2%  : deep_discount  (strongest BUY zone)
      38.2% - 50%    : discount       (BUY zone)
      50%   - 61.8%  : equilibrium    (neutral, no edge either way)
      61.8% - 78.6%  : premium        (SELL zone — also ICT's "OTE" zone)
      78.6% - 100%   : deep_premium   (strongest SELL zone)

    Used by the M1 momentum strategy. Kept separate from
    get_premium_discount() so the LG-primary engine's already-tuned
    25/50/75 gate is untouched.
    """
    if swing_high == swing_low:
        return {"zone": "equilibrium", "position_pct": 50.0}

    diff         = swing_high - swing_low
    position_pct = ((current_price - swing_low) / diff) * 100
    position_pct = max(0, min(100, position_pct))

    if position_pct <= 38.2:
        zone = "deep_discount"
    elif position_pct <= 50:
        zone = "discount"
    elif position_pct <= 61.8:
        zone = "equilibrium"
    elif position_pct <= 78.6:
        zone = "premium"
    else:
        zone = "deep_premium"

    return {
        "zone":         zone,
        "position_pct": round(position_pct, 2),
        "fib_382":      round(swing_low + diff * 0.382, 5),
        "fib_50":       round(swing_low + diff * 0.5, 5),
        "fib_618":      round(swing_low + diff * 0.618, 5),
        "fib_786":      round(swing_low + diff * 0.786, 5),
        "swing_low":    round(swing_low, 5),
        "swing_high":   round(swing_high, 5),
    }


def is_valid_zone_for_trade(zone_data: dict, bias: str) -> bool:
    """
    Vérifie que le prix est dans la bonne zone pour le trade
    BUY  → doit être en Discount
    SELL → doit être en Premium
    """
    if bias == "buy"  and zone_data["is_buy_zone"]:  return True
    if bias == "sell" and zone_data["is_sell_zone"]: return True
    return False