import pandas as pd
import numpy as np
from features.smc.structure       import detect_market_structure, detect_fresh_choch
from features.smc.orderblocks     import detect_order_blocks
from features.smc.fvg             import detect_fvg
from features.smc.liquidity       import detect_liquidity
from features.smc.premium_discount import get_premium_discount, is_valid_zone_for_trade
from features.smc.liquidity_grab  import detect_liquidity_grab, get_scalping_entry
from features.smc.cisd            import detect_cisd

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
        conditions.append(f"[OK] Structure {structure['trend']}")
    else:
        conditions.append("[NO] Structure neutral")

    # 2. CHoCH détecté (10pts bonus)
    if structure.get("last_choch"):
        score += 10
        conditions.append(f"[OK] CHoCH: {structure['last_choch']}")

    # 3. Prix dans la bonne zone P/D (20pts) ← NOUVEAU
    if is_valid_zone_for_trade(pd_zone, bias):
        score += 20
        conditions.append(f"[OK] {pd_zone['zone']} zone ({pd_zone['position_pct']}%)")
    else:
        conditions.append(f"[NO] Wrong zone: {pd_zone['zone']} ({pd_zone['position_pct']}%)")

    # 4. Order Block aligné (15pts)
    ob_count = len([ob for ob in obs if ob['type'] == bias.replace('sell', 'bearish').replace('buy', 'bullish') and ob['mitigated']])
    if ob_count > 0:
        score += 15
        conditions.append(f"[OK] {ob_count} Order Block(s) aligned")
    else:
        conditions.append("[NO] No aligned Order Block")

    # 5. FVG non rempli (10pts)
    fvg_aligned = [f for f in fvgs if f['type'] == ('bullish' if bias == 'buy' else 'bearish') and not f['filled']]
    if fvg_aligned:
        score += 10
        conditions.append(f"[OK] {len(fvg_aligned)} FVG(s) unfilled")
    else:
        conditions.append("[NO] No aligned FVG")

    # 6. OTE dans la zone (15pts)
    if ote and ote.get("in_zone"):
        score += 15
        conditions.append(f"[OK] Price in OTE zone (R:R {ote['rr_ratio']})")
    elif ote:
        score += 5
        conditions.append(f"[!] OTE exists but price not in zone")
    else:
        conditions.append("[NO] No OTE")

    # 7. Liquidity Grab ← NOUVEAU (10pts)
    if liquidity_grab.get("detected") and liquidity_grab.get("type") == ('bullish' if bias == 'buy' else 'bearish'):
        score += 10
        conditions.append(f"[OK] Liquidity Grab detected ({liquidity_grab['type']})")
    else:
        conditions.append("[NO] No Liquidity Grab")

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


def calculate_ema(df: pd.DataFrame, period: int = 50) -> float:
    """EMA — moyenne mobile exponentielle, donne le sens de la tendance de fond"""
    val = df['close'].ewm(span=period, adjust=False).mean().iloc[-1]
    return float(val)


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


def _m1_momentum(symbol: str, n: int = 15, threshold: float = 1.0) -> dict:
    """
    Raw M1 price momentum over the last `n` minutes, zero swing-point
    confirmation lag at all. Structure/CHoCH/EMA-cross all need multiple
    confirmed swing points (lookback candles AFTER they form) before they
    can flip — M1 momentum needs none of that, it just looks at where
    price actually went over the window.

    Called at two speeds (see run_scalping_analysis): n=15 catches fast
    moves; n=60 (1h) catches a slow, steady multi-hour grind that's too
    gradual for the 15-min window AND too gradual for the trading-TF's
    own 8-candle momentum check to ever cross either threshold.

    Returns {"direction": "bullish"|"bearish"|"neutral", "momentum": float}
    """
    if not symbol:
        return {"direction": "neutral", "momentum": 0.0}
    try:
        from features.market.fetcher import fetch_candles
        df_m1 = fetch_candles(symbol, "1m", limit=n + 5)
        if len(df_m1) < n + 1:
            return {"direction": "neutral", "momentum": 0.0}

        atr_m1 = calculate_atr(df_m1)
        if atr_m1 <= 0:
            return {"direction": "neutral", "momentum": 0.0}

        momentum = (float(df_m1['close'].iloc[-1]) - float(df_m1['close'].iloc[-n])) / atr_m1

        if momentum > threshold:
            direction = "bullish"
        elif momentum < -threshold:
            direction = "bearish"
        else:
            direction = "neutral"

        return {"direction": direction, "momentum": round(momentum, 3)}
    except Exception as e:
        print(f"[M1] {symbol}: fetch error — {e}")
        return {"direction": "neutral", "momentum": 0.0}


def run_scalping_analysis(df: pd.DataFrame, symbol: str = "", macro_trend: str = None) -> dict:
    """
    LG-primary scalping engine:
    [REQUIRED] Liquidity Grab — sets direction (bullish LG = BUY, bearish LG = SELL)
    [REQUIRED] P/D gate       — BUY only discount ≤50%, SELL only premium ≥50%
    [BONUS]    OB/FVG zone    — +20pts if aligned with LG direction
    [BONUS]    RSI/Stoch/EMA/Structure — confluence scoring
    """
    if len(df) < 20:
        return {"error": "Pas assez de données (minimum 20 bougies)"}

    current_price = float(df['close'].iloc[-1])
    adx           = calculate_adx(df)
    atr           = calculate_atr(df)
    atr_ratio     = calculate_atr_ratio(df)

    # ── Hard gates: dead/chaotic market ──────────────────────────────────
    if adx < 10:
        return to_python({
            "current_price": current_price, "scalping_score": 0,
            "conditions": [f"ADX {adx} < 10 — dead market, skip"],
            "should_scalp": False, "bias": "neutral",
            "adx": adx, "atr": round(atr, 5), "atr_ratio": atr_ratio,
        })
    if atr_ratio > 1.8:
        return to_python({
            "current_price": current_price, "scalping_score": 0,
            "conditions": [f"[NO] Volatilité trop haute (ATR ratio: {atr_ratio} > 1.8)"],
            "should_scalp": False, "bias": "neutral",
            "adx": adx, "atr": round(atr, 5), "atr_ratio": atr_ratio,
        })
    if atr_ratio < 0.3:
        return to_python({
            "current_price": current_price, "scalping_score": 0,
            "conditions": [f"[NO] Volatilité trop faible (ATR ratio: {atr_ratio})"],
            "should_scalp": False, "bias": "neutral",
            "adx": adx, "atr": round(atr, 5), "atr_ratio": atr_ratio,
        })

    rsi              = calculate_rsi(df)
    stoch_k, stoch_d = calculate_stochastic(df)
    ema50            = calculate_ema(df, 50)
    structure        = detect_market_structure(df)
    sh               = structure.get("last_swing_high")
    swing_low        = structure.get("last_swing_low")

    # ── PRIMARY SIGNAL: Liquidity Grab (REQUIRED) ────────────────────────
    # No LG = no trade. Direction comes from LG, not from OB/FVG zone type.
    liquidity_grab = detect_liquidity_grab(df, lookback=20)
    if not liquidity_grab.get("detected"):
        return to_python({
            "current_price": current_price, "scalping_score": 0,
            "conditions": ["No Liquidity Grab — LG required for entry"],
            "should_scalp": False, "bias": "neutral",
            "adx": adx, "atr": round(atr, 5), "atr_ratio": atr_ratio,
            "rsi": rsi, "stoch_k": stoch_k, "stoch_d": stoch_d,
            "structure": structure, "liquidity_grab": liquidity_grab,
        })

    lg_type     = liquidity_grab["type"]   # "bullish" | "bearish"
    lg_strength = float(liquidity_grab.get("strength", 0))

    # ── Trend evidence: fresh CHoCH + established structure + momentum + M1 ──
    # Computed BEFORE locking in direction, so a counter-trend LG can be
    # swapped for its alt-direction sibling (if one exists) instead of just
    # rejecting a high-confidence trend signal outright. Five lag-tiers,
    # each catches a different failure mode the others miss:
    #   1. Fresh CHoCH     — catches the EXACT candle a reversal happens
    #   2. Structure trend — catches an ALREADY-confirmed counter-trend that
    #      the fresh check goes silent on once the break isn't "new" anymore
    #      (this is what let 3 straight BTC BUYs fire into a sustained decline)
    #   3. Raw momentum    — needs zero swing confirmation on the trading TF,
    #      reacts immediately to a real move even before structure/CHoCH catch up
    #   4. M1 momentum (15min) — catches fast moves with zero swing-point lag
    #   5. M1 momentum (1h)    — catches a slow, steady multi-hour grind that's
    #      too gradual for BOTH the 15-min M1 window AND the trading-TF's own
    #      8-candle window to ever cross either threshold (confirmed root cause
    #      of a real BTC/ETH losing streak: 15-min M1 read only 0.34xATR while
    #      price ground steadily upward for 4 hours)
    fresh_choch = detect_fresh_choch(df)
    choch_dir   = fresh_choch.get("choch")
    ltf_trend   = structure.get("trend", "neutral")

    _mom_n   = 8
    momentum = 0.0
    if len(df) > _mom_n and atr > 0:
        momentum = (float(df['close'].iloc[-1]) - float(df['close'].iloc[-_mom_n])) / atr

    m1      = _m1_momentum(symbol, n=15)
    m1_slow = _m1_momentum(symbol, n=60)

    def _counter_reasons(direction: str) -> list:
        reasons = []
        if choch_dir and choch_dir != direction:
            reasons.append(f"fresh {choch_dir} CHoCH @ {fresh_choch['broke_level']:.5f}")
        if ltf_trend != "neutral" and ltf_trend != direction and choch_dir != direction:
            reasons.append(f"confirmed {ltf_trend} structure")
        if (direction == "bullish" and momentum < -1.2) or (direction == "bearish" and momentum > 1.2):
            reasons.append(f"momentum {momentum:+.2f}xATR over {_mom_n} candles")
        if m1["direction"] != "neutral" and m1["direction"] != direction:
            reasons.append(f"M1 momentum {m1['momentum']:+.2f}xATR ({m1['direction']})")
        if m1_slow["direction"] != "neutral" and m1_slow["direction"] != direction:
            reasons.append(f"M1(1h) momentum {m1_slow['momentum']:+.2f}xATR ({m1_slow['direction']})")
        return reasons

    counter_reasons = _counter_reasons(lg_type)

    # ── Pullback avec la tendance 4H : le momentum court-terme opposé EST
    # le pullback qu'on cherche à trader, pas un veto (étude contrefactuelle
    # 07-11→07-14, 232 épisodes bloqués : les entrées alignées 4H tuées par
    # ce veto gagnaient 49% WR / +0.71R). Une structure LTF confirmée
    # opposée reste un vrai veto (retournement, pas pullback).
    _ct_waived = None
    if macro_trend in ("bullish", "bearish") and lg_type == macro_trend and counter_reasons:
        _hard = [r for r in counter_reasons if "structure" in r]
        if not _hard:
            _ct_waived = " | ".join(counter_reasons)
            counter_reasons = []
        else:
            counter_reasons = _hard

    # If the trend opposes the primary LG and an alt-direction LG exists,
    # switch to it instead of wasting a high-confidence trend read — profit
    # from the correct direction rather than just standing aside.
    if counter_reasons and liquidity_grab.get("alt_type"):
        alt           = liquidity_grab["alt"]
        alt_reasons   = _counter_reasons(liquidity_grab["alt_type"])
        if not alt_reasons:
            print(f"[CHOCH] {symbol or '?'}: switching LG {lg_type}→{liquidity_grab['alt_type']} "
                  f"— trend evidence ({' | '.join(counter_reasons)}) favors the alt direction")
            liquidity_grab  = alt
            lg_type         = alt["type"]
            lg_strength     = float(alt.get("strength", 0))
            counter_reasons = []   # alt direction confirmed clean, no conflicts

    bias = "buy" if lg_type == "bullish" else "sell"

    score      = 0
    conditions = []
    if _ct_waived:
        conditions.append(f"Counter-trend waived — 4H {macro_trend} pullback ({_ct_waived})")

    # LG base score (30–40 pts depending on strength)
    lg_base = round(30 + min(10.0, 10.0 * lg_strength), 1)
    score += lg_base
    conditions.append(
        f"LG {lg_type} ({liquidity_grab.get('pattern','')}, "
        f"str={lg_strength:.3f}) +{lg_base}pts"
    )

    # ── CISD: Change in State of Delivery (bonus confirmation) ───────────
    # Confirms LG reversal by detecting delivery direction flip on the candles.
    # Fresh CISD (current candle) = +20pts, older = fewer pts. Never blocks.
    cisd = detect_cisd(df)
    cisd_confirmed = cisd.get("detected") and cisd.get("type") == lg_type
    if cisd_confirmed:
        cisd_age   = cisd.get("age", 1)
        cisd_bonus = max(10, 20 - (cisd_age - 1) * 5)  # 20→15→10 pts for age 1→2→3
        score += cisd_bonus
        conditions.append(
            f"CISD {cisd['type']} (age={cisd_age}, run={cisd.get('run_length',0)}, "
            f"str={cisd.get('strength',0):.3f}) +{cisd_bonus}pts"
        )
    else:
        conditions.append(f"No CISD ({cisd.get('description','not detected')})")

    # ── Counter-trend gate: only fires if switching to alt wasn't possible ──
    if counter_reasons:
        reason_str = " | ".join(counter_reasons)
        print(f"[CHOCH] {symbol or '?'}: REJECTED — {reason_str} opposes LG {lg_type} "
              f"(no alt-direction LG available, counter-trend blocked)")
        return to_python({
            "current_price": current_price, "scalping_score": 0,
            "conditions": [f"COUNTER-TREND REJECTED: {reason_str} opposes LG {lg_type} (no alt LG)"],
            "should_scalp": False, "bias": bias,
            "structure": structure, "liquidity_grab": liquidity_grab,
            "adx": adx, "atr": round(atr, 5),
        })
    elif choch_dir == lg_type:
        score += 15
        print(f"[CHOCH] {symbol or '?'}: {choch_dir} @ "
              f"{fresh_choch['broke_level']:.5f} confirms LG direction +15pts")
        conditions.append(
            f"CHoCH {choch_dir} @ {fresh_choch['broke_level']:.5f} "
            f"confirms LG direction +15pts"
        )
    else:
        conditions.append("No fresh CHoCH (structure continuation)")

    # ── P/D gate: hard filter (unchanged) ────────────────────────────────
    _pd_check = get_premium_discount(
        swing_low or current_price * 0.99,
        sh        or current_price * 1.01,
        current_price,
    )
    _pd_pos  = _pd_check.get("position_pct", 50)
    _pd_name = _pd_check.get("zone", "equilibrium")

    # Bandes P/D élargies quand le trade suit la tendance 4H : en tendance,
    # acheter à 70% du range = continuation, pas un "achat au sommet".
    # (étude 07-14 : buys bloqués par P/D alignés au régime = 41% WR / +0.44R)
    _buy_max  = 80 if macro_trend == "bullish" and bias == "buy"  else 65
    _sell_min = 20 if macro_trend == "bearish" and bias == "sell" else 35

    if bias == "buy" and _pd_pos > _buy_max:
        return to_python({
            "current_price": current_price, "scalping_score": 0,
            "conditions": [f"P/D REJECTED: BUY in deep premium ({_pd_name} {_pd_pos:.0f}%) — max {_buy_max}%"],
            "should_scalp": False, "bias": bias,
            "premium_discount": _pd_check, "liquidity_grab": liquidity_grab,
            "adx": adx, "atr": round(atr, 5),
        })
    if bias == "sell" and _pd_pos < _sell_min:
        return to_python({
            "current_price": current_price, "scalping_score": 0,
            "conditions": [f"P/D REJECTED: SELL in deep discount ({_pd_name} {_pd_pos:.0f}%) — min {_sell_min}%"],
            "should_scalp": False, "bias": bias,
            "premium_discount": _pd_check, "liquidity_grab": liquidity_grab,
            "adx": adx, "atr": round(atr, 5),
        })

    # P/D position bonus (only for clear discount/premium, not equilibrium zone)
    if bias == "buy":
        if _pd_pos <= 35:
            score += 15; conditions.append(f"Deep discount BUY ({_pd_name} {_pd_pos:.0f}%) +15pts")
        elif _pd_pos <= 50:
            score += 5;  conditions.append(f"Discount BUY ({_pd_name} {_pd_pos:.0f}%) +5pts")
        else:
            conditions.append(f"Equilibrium/premium BUY ({_pd_name} {_pd_pos:.0f}%) +0pts")
    else:
        if _pd_pos >= 65:
            score += 15; conditions.append(f"Deep premium SELL ({_pd_name} {_pd_pos:.0f}%) +15pts")
        elif _pd_pos >= 50:
            score += 5;  conditions.append(f"Premium SELL ({_pd_name} {_pd_pos:.0f}%) +5pts")
        else:
            conditions.append(f"Equilibrium/discount SELL ({_pd_name} {_pd_pos:.0f}%) +0pts")

    # ── ADX extreme counter-trend block ──────────────────────────────────
    if adx > 40:
        trend = structure.get("trend", "neutral")
        if (bias == "sell" and trend == "bullish") or (bias == "buy" and trend == "bearish"):
            return to_python({
                "current_price": current_price, "scalping_score": 0,
                "conditions": [f"ADX={adx:.0f} extreme trend ({trend}) — {bias} counter-trend blocked"],
                "should_scalp": False, "bias": "neutral",
                "adx": adx, "atr": round(atr, 5), "atr_ratio": atr_ratio,
                "rsi": rsi, "stoch_k": stoch_k, "stoch_d": stoch_d,
                "structure": structure,
            })

    # ── OB/FVG zone: bonus if aligned with LG direction ──────────────────
    # Direction is already set by LG. OB/FVG give extra confluence points.
    order_blocks   = detect_order_blocks(df)
    fvgs_all       = detect_fvg(df, only_unfilled=False)
    proximity      = atr * 0.5
    smc_confluence = None
    zone_low       = None
    zone_high      = None

    for ob in order_blocks:
        ob_matches = (bias == "buy"  and ob["type"] == "bullish") or \
                     (bias == "sell" and ob["type"] == "bearish")
        if not ob_matches:
            continue
        if ob["mitigated"]:
            score += 20; smc_confluence = "order_block"
            zone_low, zone_high = ob["low"], ob["high"]
            conditions.append(f"OB inside aligned ({ob['type']}, str={ob['strength']}) +20pts")
            break
        near = (bias == "buy"  and current_price >= ob["low"]  - proximity) or \
               (bias == "sell" and current_price <= ob["high"] + proximity)
        if near:
            score += 10; smc_confluence = "order_block"
            zone_low, zone_high = ob["low"], ob["high"]
            conditions.append(f"OB approaching aligned ({ob['type']}) +10pts")
            break

    if smc_confluence is None:
        for fvg in fvgs_all:
            fvg_matches = (bias == "buy"  and fvg["type"] == "bullish") or \
                          (bias == "sell" and fvg["type"] == "bearish")
            if not fvg_matches:
                continue
            if fvg["bottom"] <= current_price <= fvg["top"]:
                score += 20; smc_confluence = "fvg"
                zone_low, zone_high = fvg["bottom"], fvg["top"]
                conditions.append(f"FVG inside aligned ({fvg['type']}, size={fvg['size']}) +20pts")
                break
            near = (bias == "buy"  and current_price >= fvg["bottom"] - proximity) or \
                   (bias == "sell" and current_price <= fvg["top"]    + proximity)
            if near:
                score += 10; smc_confluence = "fvg"
                zone_low, zone_high = fvg["bottom"], fvg["top"]
                conditions.append(f"FVG approaching aligned ({fvg['type']}) +10pts")
                break

    if smc_confluence is None:
        conditions.append("No aligned OB/FVG (LG-only entry)")

    # ── EMA50 alignment ───────────────────────────────────────────────────
    ema50_aligned = (bias == "buy"  and current_price >= ema50) or \
                    (bias == "sell" and current_price <= ema50)
    if ema50_aligned:
        score += 10; conditions.append(f"EMA50 aligned ({current_price:.2f} vs {ema50:.2f}) +10pts")
    else:
        conditions.append(f"EMA50 against ({current_price:.2f} vs {ema50:.2f}) +0pts")

    # ── Dynamic ML weights ────────────────────────────────────────────────
    try:
        from features.trading.ta_optimizer import get_config as _get_ta_config
        _ta_cfg = _get_ta_config()
        _w      = _ta_cfg.get("scoring_weights", {})
        _thr    = _ta_cfg.get("thresholds", {})
    except Exception:
        _w = {}; _thr = {}

    def _w_(key, default): return float(_w.get(key, default))

    W_RSI    = _w_("rsi",       25.0)
    W_STOCH  = _w_("stoch",     25.0)
    W_STRUCT = _w_("structure", 10.0)
    W_ADX    = _w_("adx",        0.0)
    W_ATR    = _w_("atr_ratio",  0.0)

    RSI_MAX_BUY   = _thr.get("rsi_max_buy",  70)
    RSI_MIN_SELL  = _thr.get("rsi_min_sell", 30)
    STOCH_MAX_BUY = _thr.get("stoch_max_buy", 80)
    STOCH_MIN_SEL = _thr.get("stoch_min_sell", 20)

    # ── RSI ───────────────────────────────────────────────────────────────
    rsi_aligned = (bias == "buy" and rsi < RSI_MAX_BUY) or \
                  (bias == "sell" and rsi > RSI_MIN_SELL)
    if rsi_aligned:
        score += W_RSI; conditions.append(f"RSI {rsi} OK (+{round(W_RSI,1)}pts)")
    else:
        conditions.append(f"RSI {rsi} blocked {bias}")

    # ── Stochastic ────────────────────────────────────────────────────────
    stoch_aligned = (bias == "buy"  and stoch_k < STOCH_MAX_BUY) or \
                    (bias == "sell" and stoch_k > STOCH_MIN_SEL)
    stoch_cross   = (bias == "buy"  and stoch_k > stoch_d) or \
                    (bias == "sell" and stoch_k < stoch_d)
    if stoch_aligned:
        stoch_base  = round(W_STOCH * 0.8, 1)
        stoch_bonus = round(W_STOCH * 0.2, 1)
        score += stoch_base; conditions.append(f"Stoch {stoch_k:.0f} OK (+{stoch_base}pts)")
        if stoch_cross:
            score += stoch_bonus; conditions.append(f"Stoch cross +{stoch_bonus}pts")
    else:
        conditions.append(f"Stoch {stoch_k:.0f} blocked {bias}")

    # ── Structure ─────────────────────────────────────────────────────────
    if (bias == "buy"  and structure["trend"] == "bullish") or \
       (bias == "sell" and structure["trend"] == "bearish"):
        score += W_STRUCT; conditions.append(f"Structure {structure['trend']} +{round(W_STRUCT,1)}pts")
    else:
        conditions.append(f"Structure {structure['trend']}")

    # ── ADX strength bonus (ML-weighted) ─────────────────────────────────
    if W_ADX > 0:
        adx_pct = min(1.0, max(0.0, (adx - 15) / 20.0))
        adx_pts = round(W_ADX * adx_pct, 1)
        if adx_pts > 0:
            score += adx_pts; conditions.append(f"ADX {adx:.0f} +{adx_pts}pts")

    # ── ATR ratio quality (ML-weighted) ──────────────────────────────────
    if W_ATR > 0:
        atr_quality = 1.0 - min(1.0, abs(atr_ratio - 1.0))
        atr_pts = round(W_ATR * atr_quality, 1)
        if atr_pts > 0:
            score += atr_pts; conditions.append(f"ATR ratio {atr_ratio:.2f} +{atr_pts}pts")

    # ── SL/TP from LG sweep level (always available since LG is required) ─
    scalping_entry = get_scalping_entry(df, liquidity_grab, structure.get("trend", "neutral"))
    if scalping_entry:
        entry_price = current_price
        if bias == "buy":
            sl_struct = scalping_entry["sl"]
            sl_atr    = round(entry_price - atr * 1.5, 5)
            new_sl    = min(sl_struct, sl_atr)
            sl_cap    = round(entry_price - atr * 2.0, 5)
            new_sl    = max(new_sl, sl_cap)
            risk      = abs(entry_price - new_sl)
            new_tp1   = round(entry_price + risk * 2.5, 5)
            new_tp2   = round(entry_price + risk * 4.0, 5)
        else:
            sl_struct = scalping_entry["sl"]
            sl_atr    = round(entry_price + atr * 1.5, 5)
            new_sl    = max(sl_struct, sl_atr)
            sl_cap    = round(entry_price + atr * 2.0, 5)
            new_sl    = min(new_sl, sl_cap)
            risk      = abs(entry_price - new_sl)
            new_tp1   = round(entry_price - risk * 2.5, 5)
            new_tp2   = round(entry_price - risk * 4.0, 5)

        risk   = abs(entry_price - new_sl)
        reward = abs(new_tp1 - entry_price)
        scalping_entry.update({
            "entry":    round(entry_price, 5),
            "sl":       new_sl, "tp1": new_tp1, "tp2": new_tp2,
            "rr_ratio": round(reward / risk, 2) if risk > 0 else 0,
            "atr":      round(atr, 5),
        })

    should_scalp = score >= 60 and scalping_entry is not None

    return to_python({
        "current_price":    current_price,
        "scalping_score":   round(score, 1),
        "conditions":       conditions,
        "should_scalp":     should_scalp,
        "bias":             bias,
        "liquidity_grab":   liquidity_grab,
        "scalping_entry":   scalping_entry,
        "premium_discount": _pd_check,
        "structure":        structure,
        "adx":              adx,
        "atr":              round(atr, 5),
        "atr_ratio":        atr_ratio,
        "rsi":              rsi,
        "stoch_k":          stoch_k,
        "stoch_d":          stoch_d,
        "ema50":            round(ema50, 5),
        "smc_confluence":   smc_confluence,
        "cisd":             cisd,
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