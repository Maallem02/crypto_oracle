import pandas as pd
import numpy as np

def detect_swings(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """Détecte les swing highs et swing lows"""
    df = df.copy()
    df['swing_high'] = False
    df['swing_low'] = False

    for i in range(lookback, len(df) - lookback):
        window_high = df['high'].iloc[i - lookback:i + lookback + 1]
        window_low  = df['low'].iloc[i - lookback:i + lookback + 1]

        if df['high'].iloc[i] == window_high.max():
            df.at[df.index[i], 'swing_high'] = True
        if df['low'].iloc[i] == window_low.min():
            df.at[df.index[i], 'swing_low'] = True

    return df

def detect_market_structure(df: pd.DataFrame) -> dict:
    """Détecte BOS (Break of Structure) et CHoCH (Change of Character)"""
    df = detect_swings(df)

    highs = df[df['swing_high']]['high'].values
    lows  = df[df['swing_low']]['low'].values

    last_bos   = None
    last_choch = None
    trend      = "neutral"

    if len(highs) >= 2 and len(lows) >= 2:
        # Trend bullish : HH + HL
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            trend    = "bullish"
            last_bos = "bullish"
        # Trend bearish : LH + LL
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            trend    = "bearish"
            last_bos = "bearish"
        # CHoCH : inversion de structure
        elif highs[-1] < highs[-2] and lows[-1] > lows[-2]:
            last_choch = "bearish_to_bullish"
            trend      = "bullish"
        elif highs[-1] > highs[-2] and lows[-1] < lows[-2]:
            last_choch = "bullish_to_bearish"
            trend      = "bearish"

    # Dernier swing high/low connu
    last_swing_high = float(highs[-1]) if len(highs) > 0 else None
    last_swing_low  = float(lows[-1])  if len(lows) > 0  else None

    return {
        "trend":           trend,
        "last_bos":        last_bos,
        "last_choch":      last_choch,
        "last_swing_high": last_swing_high,
        "last_swing_low":  last_swing_low,
    }
