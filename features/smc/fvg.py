import pandas as pd

def detect_fvg(df: pd.DataFrame, only_unfilled: bool = True) -> list:
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
            bottom = float(c1['high'])
            top    = float(c3['low'])
            fvgs.append({
                "type":   "bullish",
                "top":    round(top, 5),
                "bottom": round(bottom, 5),
                "size":   round(float(size), 5),
                "index":  i,
                "filled": current_price >= top,   # filled when price rises through the gap
            })

        # Bearish FVG
        elif c3['high'] < c1['low']:
            size = c1['low'] - c3['high']
            bottom = float(c3['high'])
            top    = float(c1['low'])
            fvgs.append({
                "type":   "bearish",
                "top":    round(top, 5),
                "bottom": round(bottom, 5),
                "size":   round(float(size), 5),
                "index":  i,
                "filled": current_price <= bottom,  # filled when price drops through the gap
            })

    # Retourner les 5 FVG les plus récents — non remplis par défaut, ou tous
    # (only_unfilled=False) pour détecter les zones que le prix teste actuellement
    fvgs_sorted = sorted(fvgs, key=lambda x: x['index'], reverse=True)
    if only_unfilled:
        fvgs_sorted = [f for f in fvgs_sorted if not f['filled']]
    return fvgs_sorted[:5]
