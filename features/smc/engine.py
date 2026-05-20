import pandas as pd
import numpy as np
from features.smc.structure       import detect_market_structure
from features.smc.orderblocks     import detect_order_blocks
from features.smc.fvg             import detect_fvg
from features.smc.liquidity       import detect_liquidity
from features.smc.premium_discount import get_premium_discount, is_valid_zone_for_trade
from features.smc.liquidity_grab  import detect_liquidity_grab, get_scalping_entry

FIB_LEVELS = {
    "0": 0.0, "236": 0.236, "382": 0.382, "500": 0.5,
    "618": 0.618, "705": 0.705, "786": 0.786, "1": 1.0,
}

def to_python(obj):
    if isinstance(obj, dict):  return {k: to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):  return [to_python(i) for i in obj]
    if isinstance(obj, (np.bool_,)):    return bool(obj)
    if isinstance(obj, (np.integer,)):  return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
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
    sh    = structure.get("last_swing_high")
    sl    = structure.get("last_swing_low")
    trend = structure.get("trend")
    if not sh or not sl or trend == "neutral":
        return None

    fibs = compute_fibonacci(sl, sh, trend)

    if trend == "bullish":
        ote_high   = fibs["fib_618"]
        ote_low    = fibs["fib_786"]
        ob_aligned = any(
            ob['type'] == 'bullish' and ob['low'] <= ote_high and ob['high'] >= ote_low
            for ob in order_blocks)
        in_zone = ote_low <= current_price <= ote_high
        entry   = round((ote_high + ote_low) / 2, 5)
        sl_lvl  = round(fibs["fib_1"] * 0.999, 5)
        tp1     = round(sh, 5)
        tp2     = round(sh + (sh - sl) * 0.5, 5)
    else:
        ote_high   = fibs["fib_786"]
        ote_low    = fibs["fib_618"]
        ob_aligned = any(
            ob['type'] == 'bearish' and ob['low'] <= ote_high and ob['high'] >= ote_low
            for ob in order_blocks)
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

def compute_confluence_score(
    structure:      dict,
    obs:            list,
    fvgs:           list,
    ote:            dict | None,
    pd_zone:        dict,
    liquidity_grab: dict,
    bias:           str,
) -> tuple[float, list]:
    """
    Calcule un score de confluence 0-100
    Retourne (score, liste des conditions validées)
    """
    conditions = []
    score      = 0

    # 1. Structure de marché (20pts)
    if structure["trend"] != "neutral":
        score += 20
        conditions.append(f"✅ Structure {structure['trend']}")
    else:
        conditions.append("❌ Structure neutral")

    # 2. CHoCH détecté (10pts bonus)
    if structure.get("last_choch"):
        score += 10
        conditions.append(f"✅ CHoCH: {structure['last_choch']}")

    # 3. Prix dans la bonne zone P/D (20pts) ← NOUVEAU
    if is_valid_zone_for_trade(pd_zone, bias):
        score += 20
        conditions.append(f"✅ {pd_zone['zone']} zone ({pd_zone['position_pct']}%)")
    else:
        conditions.append(f"❌ Wrong zone: {pd_zone['zone']} ({pd_zone['position_pct']}%)")

    # 4. Order Block aligné (15pts)
    ob_count = len([ob for ob in obs if ob['type'] == bias.replace('sell', 'bearish').replace('buy', 'bullish') and not ob['mitigated']])
    if ob_count > 0:
        score += 15
        conditions.append(f"✅ {ob_count} Order Block(s) aligned")
    else:
        conditions.append("❌ No aligned Order Block")

    # 5. FVG non rempli (10pts)
    fvg_aligned = [f for f in fvgs if f['type'] == ('bullish' if bias == 'buy' else 'bearish') and not f['filled']]
    if fvg_aligned:
        score += 10
        conditions.append(f"✅ {len(fvg_aligned)} FVG(s) unfilled")
    else:
        conditions.append("❌ No aligned FVG")

    # 6. OTE dans la zone (15pts)
    if ote and ote.get("in_zone"):
        score += 15
        conditions.append(f"✅ Price in OTE zone (R:R {ote['rr_ratio']})")
    elif ote:
        score += 5
        conditions.append(f"⚠️ OTE exists but price not in zone")
    else:
        conditions.append("❌ No OTE")

    # 7. Liquidity Grab ← NOUVEAU (10pts)
    if liquidity_grab.get("detected") and liquidity_grab.get("type") == ('bullish' if bias == 'buy' else 'bearish'):
        score += 10
        conditions.append(f"✅ Liquidity Grab detected ({liquidity_grab['type']})")
    else:
        conditions.append("❌ No Liquidity Grab")

    return round(score, 1), conditions

def compute_bias(structure, obs, fvgs, ote, pd_zone) -> tuple[str, float]:
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

    # Premium/Discount influence
    if pd_zone["zone"] in ["discount", "deep_discount"]:   score += 0.5
    elif pd_zone["zone"] in ["premium", "deep_premium"]:   score -= 0.5

    max_score  = 5.5
    normalized = max(-1, min(1, score / max_score))
    confidence = abs(normalized)

    if normalized > 0.2:    bias = "buy"
    elif normalized < -0.2: bias = "sell"
    else:                   bias = "neutral"

    return bias, round(confidence, 2)

def run_scalping_analysis(df: pd.DataFrame) -> dict:
    """Analyse légère et rapide pour le scalping — focus Liquidity Grab"""
    if len(df) < 20:
        return {"error": "Pas assez de données (minimum 20 bougies)"}

    current_price  = float(df['close'].iloc[-1])
    structure      = detect_market_structure(df)
    liquidity_grab = detect_liquidity_grab(df, lookback=8)

    sh = structure.get("last_swing_high")
    sl = structure.get("last_swing_low")
    pd_zone = get_premium_discount(
        sl or current_price * 0.99,
        sh or current_price * 1.01,
        current_price,
    )

    score      = 0
    conditions = []

    # 1. Liquidity Grab (40pts) — signal obligatoire
    if not liquidity_grab.get("detected"):
        return to_python({
            "current_price":  current_price,
            "scalping_score": 0,
            "conditions":     ["❌ No Liquidity Grab — skip"],
            "should_scalp":   False,
            "bias":           "neutral",
            "structure":      structure,
        })

    score += 40
    conditions.append(f"✅ Liquidity Grab {liquidity_grab['type']} @ {liquidity_grab['grabbed_level']}")
    bias = "buy" if liquidity_grab["type"] == "bullish" else "sell"

    # 2. Structure confirme (30pts)
    if (bias == "buy"  and structure["trend"] in ["bullish", "neutral"]) or \
       (bias == "sell" and structure["trend"] in ["bearish", "neutral"]):
        score += 30
        conditions.append(f"✅ Structure {structure['trend']}")
    else:
        score += 10
        conditions.append(f"⚠️ Contre-tendance: structure {structure['trend']}")

    # 3. Zone P/D correcte (30pts)
    if is_valid_zone_for_trade(pd_zone, bias):
        score += 30
        conditions.append(f"✅ Zone {pd_zone['zone']} ({pd_zone['position_pct']}%)")
    else:
        conditions.append(f"❌ Zone {pd_zone['zone']} ({pd_zone['position_pct']}%)")

    scalping_entry = get_scalping_entry(df, liquidity_grab, structure.get("trend", "neutral"))
    should_scalp   = score >= 60 and scalping_entry is not None

    return to_python({
        "current_price":    current_price,
        "scalping_score":   round(score, 1),
        "conditions":       conditions,
        "should_scalp":     should_scalp,
        "bias":             bias,
        "liquidity_grab":   liquidity_grab,
        "scalping_entry":   scalping_entry,
        "premium_discount": pd_zone,
        "structure":        structure,
    })


def run_smc_analysis(df: pd.DataFrame) -> dict:
    if len(df) < 50:
        return {"error": "Pas assez de données (minimum 50 bougies)"}

    current_price  = float(df['close'].iloc[-1])
    structure      = detect_market_structure(df)
    order_blocks   = detect_order_blocks(df)
    fvgs           = detect_fvg(df)
    liquidity      = detect_liquidity(df)
    liquidity_grab = detect_liquidity_grab(df)

    # Premium/Discount
    sh = structure.get("last_swing_high")
    sl = structure.get("last_swing_low")
    pd_zone = get_premium_discount(sl or current_price * 0.99, sh or current_price * 1.01, current_price)

    ote        = compute_ote(structure, order_blocks, current_price)
    bias, conf = compute_bias(structure, order_blocks, fvgs, ote, pd_zone)

    # Scalping entry basée sur Liquidity Grab
    scalping_entry = get_scalping_entry(df, liquidity_grab, structure.get("trend", "neutral"))

    # Score de confluence
    confluence_score, conditions = compute_confluence_score(
        structure, order_blocks, fvgs, ote, pd_zone, liquidity_grab, bias)

    # Signal final — trade seulement si confluence >= 65
    trade_signal = "strong" if confluence_score >= 75 else \
                   "moderate" if confluence_score >= 55 else "weak"

    result = {
        "current_price":    current_price,
        "structure":        structure,
        "order_blocks":     order_blocks,
        "fvg":              fvgs,
        "liquidity":        liquidity,
        "liquidity_grab":   liquidity_grab,
        "premium_discount": pd_zone,
        "ote":              ote,
        "scalping_entry":   scalping_entry,
        "bias":             bias,
        "confidence":       conf,
        "confluence_score": confluence_score,
        "conditions":       conditions,
        "trade_signal":     trade_signal,
    }
    return to_python(result)