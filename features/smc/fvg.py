import pandas as pd

def detect_fvg(df: pd.DataFrame) -> list:
    """
    Fair Value Gaps : espace entre la mèche d'une bougie et le corps de la suivante+1
    - Bullish FVG : low[i+2] > high[i]  (gap vers le haut)
    - Bearish FVG : high[i+2] < low[i]  (gap vers le bas)
    """
    fvgs = []
    current_price = float(df['close'].iloc[-1])

    for i in range(len(df) - 2):
        c1 = df.iloc[i]
        c3 = df.iloc[i + 2]

        # Bullish FVG
        if c3['low'] > c1['high']:
            size = c3['low'] - c1['high']
            fvgs.append({
                "type":   "bullish",
                "top":    round(float(c3['low']), 5),
                "bottom": round(float(c1['high']), 5),
                "size":   round(float(size), 5),
                "index":  i,
                "filled": current_price <= c3['low'],
            })

        # Bearish FVG
        elif c3['high'] < c1['low']:
            size = c1['low'] - c3['high']
            fvgs.append({
                "type":   "bearish",
                "top":    round(float(c1['low']), 5),
                "bottom": round(float(c3['high']), 5),
                "size":   round(float(size), 5),
                "index":  i,
                "filled": current_price >= c1['low'],
            })

    # Retourner les 5 FVG les plus récents non remplis
    fvgs_sorted = sorted(fvgs, key=lambda x: x['index'], reverse=True)
    unfilled    = [f for f in fvgs_sorted if not f['filled']]
    return unfilled[:5]
