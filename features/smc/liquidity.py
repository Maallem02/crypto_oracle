import pandas as pd
import numpy as np

def detect_liquidity(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Zones de liquidité = clusters de swing highs/lows
    Buy-side  : au-dessus des swing highs (stops des vendeurs)
    Sell-side : en-dessous des swing lows  (stops des acheteurs)
    """
    recent = df.tail(lookback)

    # Swing highs/lows simples
    buy_side  = []
    sell_side = []

    for i in range(2, len(recent) - 2):
        h = recent.iloc[i]['high']
        l = recent.iloc[i]['low']

        if h > recent.iloc[i-1]['high'] and h > recent.iloc[i+1]['high']:
            buy_side.append(round(float(h), 5))
        if l < recent.iloc[i-1]['low'] and l < recent.iloc[i+1]['low']:
            sell_side.append(round(float(l), 5))

    # Dédupliquer les niveaux proches (cluster à 0.1%)
    def cluster(levels: list, threshold: float = 0.001) -> list:
        if not levels:
            return []
        levels = sorted(set(levels))
        clusters = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - clusters[-1]) / clusters[-1] > threshold:
                clusters.append(lvl)
        return clusters

    return {
        "buy_side":  cluster(buy_side)[-3:],   # 3 zones au-dessus
        "sell_side": cluster(sell_side)[:3],    # 3 zones en-dessous
    }
