import pandas as pd
import numpy as np
from features.smc.structure   import detect_market_structure
from features.smc.orderblocks import detect_order_blocks
from features.smc.fvg         import detect_fvg
from features.smc.liquidity   import detect_liquidity

FIB_LEVELS = {
    "0": 0.0, "236": 0.236, "382": 0.382, "500": 0.5,
    "618": 0.618, "705": 0.705, "786": 0.786, "1": 1.0,
}

def to_python(obj):
    """Convertit récursivement les types numpy en types Python natifs"""
    if isinstance(obj, dict):
        return {k: to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_python(i) for i in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj

def compute_fibonacci(swing_low: float, swing_high: float, direction: str) -> dict:
    diff = swing_high - swing_low
    fibs = {}
    if direction == "bullish":
        for name, ratio in FIB_LEVELS.items():
            fibs[f"fib_{name}"] = round(swing_high - diff * ratio, 5)
    else:
        for name, ratio in FIB_LEVELS.items():
            fibs[f"fib_{name}"] = round(swing_low + diff * ratio, 5)
    return fibs

def compute_ote(structure: dict, order_blocks: list, current_price: float):
    sh = structure.get("last_swing_high")
    sl = structure.get("last_swing_low")
    trend = structure.get("trend")
    if not sh or not sl or trend == "neutral":
        return None

    fibs = compute_fibonacci(sl, sh, trend)

    if trend == "bullish":
        ote_high = fibs["fib_618"]
        ote_low  = fibs["fib_786"]
        ob_aligned = any(
            ob['type'] == 'bullish' and ob['low'] <= ote_high and ob['high'] >= ote_low
            for ob in order_blocks
        )
        in_zone = ote_low <= current_price <= ote_high
        entry   = round((ote_high + ote_low) / 2, 5)
        sl_lvl  = round(fibs["fib_1"] * 0.999, 5)
        tp1     = round(sh, 5)
        tp2     = round(sh + (sh - sl) * 0.5, 5)
    else:
        ote_high = fibs["fib_786"]
        ote_low  = fibs["fib_618"]
        ob_aligned = any(
            ob['type'] == 'bearish' and ob['low'] <= ote_high and ob['high'] >= ote_low
            for ob in order_blocks
        )
        in_zone = ote_low <= current_price <= ote_high
        entry   = round((ote_high + ote_low) / 2, 5)
        sl_lvl  = round(fibs["fib_1"] * 1.001, 5)
        tp1     = round(sl, 5)
        tp2     = round(sl - (sh - sl) * 0.5, 5)

    risk   = abs(entry - sl_lvl)
    reward = abs(tp1 - entry)
    rr     = round(reward / risk, 2) if risk > 0 else 0

    confidence = 0.5
    if ob_aligned: confidence += 0.25
    if in_zone:    confidence += 0.15
    if rr >= 2:    confidence += 0.10

    return {
        "zone_high":   round(ote_high, 5),
        "zone_low":    round(ote_low, 5),
        "fib_618":     fibs["fib_618"],
        "fib_705":     fibs["fib_705"],
        "fib_786":     fibs["fib_786"],
        "ob_aligned":  bool(ob_aligned),
        "in_zone":     bool(in_zone),
        "entry_price": entry,
        "sl":          sl_lvl,
        "tp1":         tp1,
        "tp2":         tp2,
        "rr_ratio":    rr,
        "confidence":  round(confidence, 2),
    }

def compute_bias(structure, obs, fvgs, ote):
    score = 0.0
    if structure["trend"] == "bullish":  score += 2.0
    elif structure["trend"] == "bearish": score -= 2.0
    if structure["last_choch"]:
        if "bullish" in structure["last_choch"]: score += 1.0
        else: score -= 1.0
    for ob in obs[:2]:
        if ob["type"] == "bullish" and not ob["mitigated"]: score += ob["strength"]
        elif ob["type"] == "bearish" and not ob["mitigated"]: score -= ob["strength"]
    for fvg in fvgs[:2]:
        if fvg["type"] == "bullish": score += 0.3
        else: score -= 0.3
    if ote and ote["ob_aligned"]:
        if structure["trend"] == "bullish": score += 0.5
        else: score -= 0.5

    max_score  = 5.0
    normalized = max(-1, min(1, score / max_score))
    confidence = abs(normalized)
    if normalized > 0.2:    bias = "buy"
    elif normalized < -0.2: bias = "sell"
    else:                   bias = "neutral"
    return bias, round(confidence, 2)

def run_smc_analysis(df: pd.DataFrame) -> dict:
    if len(df) < 50:
        return {"error": "Pas assez de données (minimum 50 bougies)"}

    current_price = float(df['close'].iloc[-1])
    structure     = detect_market_structure(df)
    order_blocks  = detect_order_blocks(df)
    fvgs          = detect_fvg(df)
    liquidity     = detect_liquidity(df)
    ote           = compute_ote(structure, order_blocks, current_price)
    bias, conf    = compute_bias(structure, order_blocks, fvgs, ote)

    result = {
        "current_price": current_price,
        "structure":     structure,
        "order_blocks":  order_blocks,
        "fvg":           fvgs,
        "liquidity":     liquidity,
        "ote":           ote,
        "bias":          bias,
        "confidence":    conf,
    }
    return to_python(result)
