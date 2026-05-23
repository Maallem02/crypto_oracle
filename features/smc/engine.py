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

def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    Average Directional Index — mesure la FORCE de la tendance
    ADX < 20 → marché ranging   → éviter de scalper
    ADX > 25 → marché trending  → bon pour scalper
    ADX > 40 → tendance forte   → excellent
    """
    df = df.copy()
    df['up_move']   =  df['high'].diff()
    df['down_move'] = -df['low'].diff()

    df['+dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0),   df['up_move'],   0.0)
    df['-dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0.0)

    prev_close = df['close'].shift(1)
    df['tr'] = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low']  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder smoothing (EMA avec alpha=1/period)
    atr_s = df['tr'].ewm(alpha=1/period, adjust=False).mean()
    pdm_s = df['+dm'].ewm(alpha=1/period, adjust=False).mean()
    mdm_s = df['-dm'].ewm(alpha=1/period, adjust=False).mean()

    pdi = 100 * pdm_s / atr_s.replace(0, np.nan)
    mdi = 100 * mdm_s / atr_s.replace(0, np.nan)
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, adjust=False).mean()

    val = adx.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else 20.0


def calculate_atr_ratio(df: pd.DataFrame) -> float:
    """
    Ratio ATR court terme / ATR long terme
    < 0.5 → marché trop calme   (faux mouvements)
    > 2.5 → marché trop chaotique (SL aléatoires)
    Idéal : entre 0.5 et 2.0
    """
    atr_short = calculate_atr(df.tail(20), period=7)
    atr_long  = calculate_atr(df.tail(60), period=20)
    if atr_long == 0:
        return 1.0
    return round(atr_short / atr_long, 2)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """
    RSI — mesure si le marché est suracheté ou survendu
    RSI < 30 → survendu  → bon pour BUY
    RSI > 70 → suracheté → bon pour SELL
    RSI ~ 50 → neutre
    """
    delta  = df['close'].diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 2) if not np.isnan(val) else 50.0


def calculate_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> tuple[float, float]:
    """
    Stochastique — compare le prix au range des N dernières bougies
    %K < 20 → survendu  → bon pour BUY
    %K > 80 → suracheté → bon pour SELL
    Croisement %K/%D → signal d'entrée précis
    """
    low_min  = df['low'].rolling(k_period).min()
    high_max = df['high'].rolling(k_period).max()
    diff     = high_max - low_min
    k = 100 * (df['close'] - low_min) / diff.replace(0, np.nan)
    d = k.rolling(d_period).mean()
    k_val = k.iloc[-1]
    d_val = d.iloc[-1]
    k_val = round(float(k_val), 2) if not np.isnan(k_val) else 50.0
    d_val = round(float(d_val), 2) if not np.isnan(d_val) else 50.0
    return k_val, d_val


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range — mesure la volatilité pour calibrer SL/TP"""
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low']  - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    # Fallback si pas assez de données
    return float(val) if not np.isnan(val) else float((df['high'] - df['low']).mean())


def run_scalping_analysis(df: pd.DataFrame) -> dict:
    """
    Analyse scalping améliorée :
    ✅ Liquidity Grab uniquement (Micro BOS supprimé — trop de faux signaux)
    ✅ SL/TP basés sur ATR (s'adapte à la volatilité)
    ✅ Score minimum 80 (plus strict)
    ✅ Structure doit confirmer (pas de contre-tendance)
    """
    if len(df) < 20:
        return {"error": "Pas assez de données (minimum 20 bougies)"}

    current_price  = float(df['close'].iloc[-1])
    adx            = calculate_adx(df)
    atr            = calculate_atr(df)
    atr_ratio      = calculate_atr_ratio(df)

    # ── Filtre ADX : marché trop calme → skip ────────────────────────────
    # Seuil abaissé à 15 (était 20) : ADX 15-20 est acceptable pour le scalping
    # crypto où la volatilité est structurellement plus haute.
    if adx < 15:
        return to_python({
            "current_price":  current_price,
            "scalping_score": 0,
            "conditions":     [f"ADX {adx} < 15 - ranging, skip"],
            "should_scalp":   False,
            "bias":           "neutral",
            "adx":            adx,
            "atr":            round(atr, 5),
            "atr_ratio":      atr_ratio,
        })

    # ── Filtre ATR ratio : volatilité anormale → skip ────────────────────
    if atr_ratio > 2.5:
        return to_python({
            "current_price":  current_price,
            "scalping_score": 0,
            "conditions":     [f"❌ Volatilité trop haute (ATR ratio: {atr_ratio})"],
            "should_scalp":   False,
            "bias":           "neutral",
            "adx":            adx,
            "atr":            round(atr, 5),
            "atr_ratio":      atr_ratio,
        })
    if atr_ratio < 0.3:
        return to_python({
            "current_price":  current_price,
            "scalping_score": 0,
            "conditions":     [f"❌ Volatilité trop faible (ATR ratio: {atr_ratio})"],
            "should_scalp":   False,
            "bias":           "neutral",
            "adx":            adx,
            "atr":            round(atr, 5),
            "atr_ratio":      atr_ratio,
        })

    rsi            = calculate_rsi(df)
    stoch_k, stoch_d = calculate_stochastic(df)
    structure      = detect_market_structure(df)
    liquidity_grab = detect_liquidity_grab(df, lookback=20)

    sh = structure.get("last_swing_high")
    sl = structure.get("last_swing_low")
    pd_zone = get_premium_discount(
        sl or current_price * 0.99,
        sh or current_price * 1.01,
        current_price,
    )

    score      = 0
    conditions = []

    # ── Signal obligatoire : Liquidity Grab ─────────────────── 50pts
    if not liquidity_grab.get("detected"):
        return to_python({
            "current_price":  current_price,
            "scalping_score": 0,
            "conditions":     [f"❌ No Liquidity Grab"],
            "should_scalp":   False,
            "bias":           "neutral",
            "adx":            adx,
            "atr":            round(atr, 5),
            "atr_ratio":      atr_ratio,
            "rsi":            rsi,
            "stoch_k":        stoch_k,
            "stoch_d":        stoch_d,
            "structure":      structure,
        })

    score += 50
    bias   = "buy" if liquidity_grab["type"] == "bullish" else "sell"
    conditions.append(f"✅ LG {liquidity_grab['type']} @ {liquidity_grab['grabbed_level']}")

    # ── RSI confirme ─────────────────────────────────────────── 25pts
    # BUY : RSI pas suracheté (< 70) — idéalement sous 55 = momentum pas épuisé
    # SELL: RSI pas survendu  (> 30) — idéalement au-dessus de 45
    rsi_aligned = (bias == "buy" and rsi < 70) or (bias == "sell" and rsi > 30)
    if rsi_aligned:
        score += 25
        conditions.append(f"RSI {rsi} OK for {bias}")
    else:
        conditions.append(f"RSI {rsi} blocked {bias}")

    # ── Stochastique confirme ────────────────────────────────── 25pts
    # BUY : stoch pas en territoire suracheté  (< 80)
    # SELL: stoch pas en territoire survendu   (> 20)
    # Bonus +5 si croisement K > D (buy) ou K < D (sell)
    stoch_aligned = (bias == "buy" and stoch_k < 80) or (bias == "sell" and stoch_k > 20)
    stoch_cross   = (bias == "buy" and stoch_k > stoch_d) or (bias == "sell" and stoch_k < stoch_d)
    if stoch_aligned:
        score += 20
        conditions.append(f"Stoch %K {stoch_k} OK for {bias}")
        if stoch_cross:
            score += 5
            conditions.append(f"Stoch cross {stoch_k:.1f}/{stoch_d:.1f} confirms {bias}")
    else:
        conditions.append(f"Stoch %K {stoch_k} blocked {bias}")

    # ── Bonus structure ──────────────────────────────────────── +10pts
    if (bias == "buy"  and structure["trend"] == "bullish") or \
       (bias == "sell" and structure["trend"] == "bearish"):
        score += 10
        conditions.append(f"✅ Structure {structure['trend']} (bonus +10)")
    else:
        conditions.append(f"ℹ️ Structure {structure['trend']}")

    # ── Calcul entrée + SL/TP ────────────────────────────────────────────────
    # IMPORTANT : on entre AU MARCHÉ (current_price), pas au grabbed_level.
    # SL structure = derrière la mèche (fourni par get_scalping_entry).
    # On override ensuite avec ATR si le SL structure est trop loin.
    scalping_entry = get_scalping_entry(df, liquidity_grab, structure.get("trend", "neutral"))
    if scalping_entry:
        entry_price = current_price  # TOUJOURS le prix actuel (market order)

        if bias == "buy":
            # SL = le plus serré entre : SL structure (mèche) et 1.5×ATR
            sl_struct = scalping_entry["sl"]          # derrière la mèche
            sl_atr    = round(entry_price - atr * 1.5, 5)
            new_sl    = max(sl_struct, sl_atr)        # le plus haut des deux (le moins loin)
            new_tp1   = round(entry_price + atr * 2.0, 5)
            new_tp2   = round(entry_price + atr * 3.5, 5)
        else:
            sl_struct = scalping_entry["sl"]
            sl_atr    = round(entry_price + atr * 1.5, 5)
            new_sl    = min(sl_struct, sl_atr)        # le plus bas des deux
            new_tp1   = round(entry_price - atr * 2.0, 5)
            new_tp2   = round(entry_price - atr * 3.5, 5)

        risk   = abs(entry_price - new_sl)
        reward = abs(new_tp1 - entry_price)
        scalping_entry.update({
            "entry":    round(entry_price, 5),
            "sl":       new_sl,
            "tp1":      new_tp1,
            "tp2":      new_tp2,
            "rr_ratio": round(reward / risk, 2) if risk > 0 else 0,
            "atr":      round(atr, 5),
        })

    # Seuil minimum : LG (50) + RSI (25) + Stoch (20) = 95 atteignable sans bonus.
    # On fixe le plancher à 75 ici ; le vrai filtre par min_score est dans le router.
    should_scalp = score >= 75 and scalping_entry is not None

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
        "adx":              adx,
        "atr":              round(atr, 5),
        "atr_ratio":        atr_ratio,
        "rsi":              rsi,
        "stoch_k":          stoch_k,
        "stoch_d":          stoch_d,
    })


def run_smc_analysis(df: pd.DataFrame) -> dict:
    if len(df) < 50:
        return {"error": "Pas assez de données (minimum 50 bougies)"}

    current_price  = float(df['close'].iloc[-1])
    atr            = calculate_atr(df)          # utilisé pour le fallback SL dans le router
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
        "atr":              round(atr, 5),    # exposé pour que le router puisse limiter le SL fallback
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