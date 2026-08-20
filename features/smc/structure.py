import pandas as pd
import numpy as np



def detect_swings(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """
    Marque chaque bougie qui est le plus haut (ou plus bas) de sa fenêtre
    centrée de 2*lookback+1 bougies.

    Version vectorisée (2026-08-01). L'ancienne implémentation bouclait en
    Python sur chaque bougie en re-découpant deux slices pandas + .max()/.min()
    à chaque tour : ~250 opérations pandas par appel, et la fonction est
    appelée deux fois sur la même frame à chaque analyse (une fois par
    detect_market_structure, une fois par detect_fresh_choch).

    Strictement équivalent à l'ancienne boucle :
      - rolling(2*lookback+1, center=True) EST la fenêtre iloc[i-lb : i+lb+1]
      - les bords sont NaN, et `valeur >= NaN` vaut False — ce qui reproduit
        exactement l'ancien range(lookback, len(df)-lookback)
      - >= / <= conservés pour gérer les doubles hauts/bas (deux bougies au
        même niveau comptent toutes les deux)
    """
    df = df.copy()

    if len(df) < lookback * 2 + 1:
        df['swing_high'] = False
        df['swing_low']  = False
        return df

    window = lookback * 2 + 1
    df['swing_high'] = df['high'] >= df['high'].rolling(window, center=True).max()
    df['swing_low']  = df['low']  <= df['low'].rolling(window, center=True).min()

    return df

def detect_market_structure(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 20:
        return {
            "trend": "neutral", "last_bos": None,
            "last_choch": None, "last_swing_high": None, "last_swing_low": None,
        }
    
    df = detect_swings(df)
    highs = df[df['swing_high']]['high'].values
    lows  = df[df['swing_low']]['low'].values
    
    last_bos   = None
    last_choch = None
    trend      = "neutral"
    
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            trend    = "bullish"
            last_bos = "bullish"
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            trend    = "bearish"
            last_bos = "bearish"
        elif highs[-1] < highs[-2] and lows[-1] > lows[-2]:
            last_choch = "bearish_to_bullish"
            trend      = "bullish"
        elif highs[-1] > highs[-2] and lows[-1] < lows[-2]:
            last_choch = "bullish_to_bearish"
            trend      = "bearish"
    
    last_swing_high = float(highs[-1]) if len(highs) > 0 else None
    last_swing_low  = float(lows[-1])  if len(lows) > 0  else None

    return {
        "trend":           trend,
        "last_bos":        last_bos,
        "last_choch":      last_choch,
        "last_swing_high": last_swing_high,
        "last_swing_low":  last_swing_low,
    }


def detect_fresh_choch(df: pd.DataFrame, lookback: int = 5, recent_candles: int = 6) -> dict:
    """
    Fast structure-break detector — catches a CHoCH the moment price closes
    beyond the last CONFIRMED opposite swing point, instead of waiting for a
    NEW swing point to form and confirm (which needs `lookback` candles after
    it forms — a multi-hour lag on 30m/1H).

    Returns {"choch": "bullish"|"bearish"|None, "broke_level": float|None}
    """
    if df is None or len(df) < lookback * 2 + recent_candles + 1:
        return {"choch": None, "broke_level": None}

    df_sw = detect_swings(df, lookback)

    high_idx = [i for i in range(len(df_sw)) if df_sw['swing_high'].iloc[i]]
    low_idx  = [i for i in range(len(df_sw)) if df_sw['swing_low'].iloc[i]]

    if not high_idx or not low_idx:
        return {"choch": None, "broke_level": None}

    last_high_pos = high_idx[-1]
    last_low_pos  = low_idx[-1]
    last_high     = float(df_sw['high'].iloc[last_high_pos])
    last_low      = float(df_sw['low'].iloc[last_low_pos])

    recent_closes = df_sw['close'].iloc[-recent_candles:]

    # Uptrend structure (last confirmed point is a higher low) →
    # bearish CHoCH = price closes below that swing low
    if last_low_pos > last_high_pos and (recent_closes < last_low).any():
        return {"choch": "bearish", "broke_level": last_low}

    # Downtrend structure (last confirmed point is a lower high) →
    # bullish CHoCH = price closes above that swing high
    if last_high_pos > last_low_pos and (recent_closes > last_high).any():
        return {"choch": "bullish", "broke_level": last_high}

    return {"choch": None, "broke_level": None}