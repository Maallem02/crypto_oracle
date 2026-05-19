import pandas as pd
import numpy as np

def detect_order_blocks(df: pd.DataFrame, min_strength: float = 0.6) -> list:
    """
    Détecte les Order Blocks :
    - Bullish OB : dernière bougie bearish avant un mouvement bullish fort
    - Bearish OB : dernière bougie bullish avant un mouvement bearish fort
    """
    obs = []

    for i in range(1, len(df) - 3):
        candle      = df.iloc[i]
        next_candles = df.iloc[i + 1:i + 4]

        body_size  = abs(candle['close'] - candle['open'])
        range_size = candle['high'] - candle['low']
        if range_size == 0:
            continue
        body_ratio = body_size / range_size

        # Bougie bearish suivie de 3 bougies bullish fortes
        if candle['close'] < candle['open']:
            bullish_follow = (next_candles['close'] > next_candles['open']).sum()
            move = (next_candles['high'].max() - candle['low']) / candle['low'] * 100
            if bullish_follow >= 2 and move > 0.3:
                strength = min(0.5 + (move / 2), 1.0)
                if strength >= min_strength:
                    obs.append({
                        "type":     "bullish",
                        "high":     round(float(candle['high']), 5),
                        "low":      round(float(candle['low']), 5),
                        "open":     round(float(candle['open']), 5),
                        "close":    round(float(candle['close']), 5),
                        "index":    i,
                        "strength": round(strength, 2),
                        "mitigated": False,
                    })

        # Bougie bullish suivie de 3 bougies bearish fortes
        elif candle['close'] > candle['open']:
            bearish_follow = (next_candles['close'] < next_candles['open']).sum()
            move = (candle['high'] - next_candles['low'].min()) / candle['high'] * 100
            if bearish_follow >= 2 and move > 0.3:
                strength = min(0.5 + (move / 2), 1.0)
                if strength >= min_strength:
                    obs.append({
                        "type":     "bearish",
                        "high":     round(float(candle['high']), 5),
                        "low":      round(float(candle['low']), 5),
                        "open":     round(float(candle['open']), 5),
                        "close":    round(float(candle['close']), 5),
                        "index":    i,
                        "strength": round(strength, 2),
                        "mitigated": False,
                    })

    # Vérifier si les OB sont mitigés (prix est passé dedans)
    current_price = float(df['close'].iloc[-1])
    for ob in obs:
        if ob['low'] <= current_price <= ob['high']:
            ob['mitigated'] = True

    # Retourner les 5 OB les plus récents et forts
    obs_sorted = sorted(obs, key=lambda x: (x['index'], x['strength']), reverse=True)
    return obs_sorted[:5]
