import pandas as pd
import numpy as np

def detect_liquidity_grab(df: pd.DataFrame, lookback: int = 10) -> dict:
    """
    Détecte un Liquidity Grab (Stop Hunt) récent
    
    Bullish Liquidity Grab :
    - Prix passe SOUS un swing low récent
    - Bougie ferme AU DESSUS de ce swing low
    - = institutions ont pris les stops → BUY signal
    
    Bearish Liquidity Grab :
    - Prix passe AU DESSUS d'un swing high récent
    - Bougie ferme EN DESSOUS de ce swing high
    - = institutions ont pris les stops → SELL signal
    """
    if len(df) < lookback + 5:
        return {"detected": False, "type": None}

    recent = df.tail(lookback + 5)
    last_5 = df.tail(5)

    # Swing lows/highs récents
    swing_lows  = []
    swing_highs = []

    for i in range(2, len(recent) - 2):
        if recent.iloc[i]['low'] < recent.iloc[i-1]['low'] and \
           recent.iloc[i]['low'] < recent.iloc[i+1]['low']:
            swing_lows.append(recent.iloc[i]['low'])

        if recent.iloc[i]['high'] > recent.iloc[i-1]['high'] and \
           recent.iloc[i]['high'] > recent.iloc[i+1]['high']:
            swing_highs.append(recent.iloc[i]['high'])

    if not swing_lows and not swing_highs:
        return {"detected": False, "type": None}

    last_candle  = df.iloc[-1]
    prev_candle  = df.iloc[-2]

    # Bullish Liquidity Grab
    if swing_lows:
        nearest_low = min(swing_lows, key=lambda x: abs(x - last_candle['close']))
        if (prev_candle['low'] < nearest_low and      # price went below swing low
            last_candle['close'] > nearest_low and    # closed back above
            last_candle['close'] > last_candle['open']):  # bullish close
            return {
                "detected":    True,
                "type":        "bullish",
                "grabbed_level": round(float(nearest_low), 5),
                "strength":    round(abs(prev_candle['low'] - nearest_low) / nearest_low * 100, 4),
                "description": f"Bullish LG: price swept {round(float(nearest_low), 5)} and recovered",
            }

    # Bearish Liquidity Grab
    if swing_highs:
        nearest_high = min(swing_highs, key=lambda x: abs(x - last_candle['close']))
        if (prev_candle['high'] > nearest_high and    # price went above swing high
            last_candle['close'] < nearest_high and   # closed back below
            last_candle['close'] < last_candle['open']):  # bearish close
            return {
                "detected":    True,
                "type":        "bearish",
                "grabbed_level": round(float(nearest_high), 5),
                "strength":    round(abs(prev_candle['high'] - nearest_high) / nearest_high * 100, 4),
                "description": f"Bearish LG: price swept {round(float(nearest_high), 5)} and reversed",
            }

    return {"detected": False, "type": None, "description": "No liquidity grab detected"}

def get_scalping_entry(df: pd.DataFrame, grab: dict, structure_trend: str) -> dict | None:
    """
    Calcule l'entrée de scalping basée sur le Liquidity Grab
    
    Après un bullish grab → entrée BUY au retest du niveau grabé
    Après un bearish grab → entrée SELL au retest du niveau grabé
    """
    if not grab.get("detected"):
        return None

    last_candle   = df.iloc[-1]
    current_price = float(last_candle['close'])
    grabbed_level = grab["grabbed_level"]
    grab_type     = grab["type"]

    if grab_type == "bullish":
        entry  = grabbed_level                              # entrée au niveau grabé
        sl     = round(float(df.tail(5)['low'].min()) * 0.999, 5)  # sous le plus bas récent
        tp1    = round(current_price + (current_price - sl) * 1.5, 5)  # RR 1.5
        tp2    = round(current_price + (current_price - sl) * 3.0, 5)  # RR 3.0
        action = "buy"

    else:  # bearish
        entry  = grabbed_level
        sl     = round(float(df.tail(5)['high'].max()) * 1.001, 5)
        tp1    = round(current_price - (sl - current_price) * 1.5, 5)
        tp2    = round(current_price - (sl - current_price) * 3.0, 5)
        action = "sell"

    risk   = abs(entry - sl)
    reward = abs(tp1 - entry)
    rr     = round(reward / risk, 2) if risk > 0 else 0

    return {
        "action":        action,
        "entry":         round(entry, 5),
        "sl":            sl,
        "tp1":           tp1,
        "tp2":           tp2,
        "rr_ratio":      rr,
        "grab_level":    grabbed_level,
        "description":   grab.get("description", ""),
    }