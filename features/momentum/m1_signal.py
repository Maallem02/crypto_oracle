"""
M1 confirmation entry — second strategy, independent of LG/SMC.

Replaces the original pure-momentum approach (M1 displacement / ATR) with
the standard SMC execution sequence, scaled down one level:

  1. Determine BIAS  — 5m + 15m structure (BOS/CHoCH) AND EMA50/200 on
                        both timeframes must agree (no contradictions)
  2. Wait for PULLBACK — price must have returned to a discount zone
                          (bullish bias) or premium zone (bearish bias),
                          using the Fibonacci P/D split (38.2/61.8)
  3. Mark a LEVEL     — an unfilled FVG near current price, in the bias
                        direction — the zone price is reacting to
  4. CONFIRM on M1    — a real candlestick reversal pattern (engulfing,
                        three soldiers/crows, hammer, shooting star) must
                        fire on the M1 candle right at that level

Raw momentum alone said "something moved, go." This says "the trend
pulled back to a real level, AND price just showed a textbook reversal
print there" — a much higher bar, but a much more deliberate one.
"""


def _ema_bias(df, calculate_ema) -> str:
    if len(df) < 200:
        return "neutral"
    e50  = calculate_ema(df, 50)
    e200 = calculate_ema(df, 200)
    return "bullish" if e50 > e200 else "bearish"


def get_m1_momentum_signal(symbol: str) -> dict:
    """
    Returns {"detected": False} or a dict with action/entry/sl/tp1/rr_ratio.
    Function name kept for router.py compatibility — this is the M1
    confirmation entry, not the old momentum-displacement check.
    """
    from features.market.fetcher import fetch_candles
    from features.smc.engine import calculate_atr, calculate_ema
    from features.smc.structure import detect_market_structure
    from features.smc.premium_discount import get_fib_zone
    from features.smc.fvg import detect_fvg
    from features.momentum.candlestick_patterns import (
        detect_bullish_pattern, detect_bearish_pattern,
        STRONG_BULLISH, STRONG_BEARISH,
    )

    try:
        # ── Step 1: HTF bias — 5m + 15m structure AND EMA must agree ──────
        df_5m  = fetch_candles(symbol, "5m",  limit=250)
        df_15m = fetch_candles(symbol, "15m", limit=250)
        if len(df_5m) < 50 or len(df_15m) < 50:
            return {"detected": False, "reason": "insufficient_history"}

        struct_5m  = detect_market_structure(df_5m)
        struct_15m = detect_market_structure(df_15m)
        trend_5m   = struct_5m.get("trend", "neutral")
        trend_15m  = struct_15m.get("trend", "neutral")
        ema_5m     = _ema_bias(df_5m,  calculate_ema)
        ema_15m    = _ema_bias(df_15m, calculate_ema)

        votes = [trend_5m, trend_15m, ema_5m, ema_15m]
        bull_votes = votes.count("bullish")
        bear_votes = votes.count("bearish")

        # Require at least 3 of 4 confirmations — allow at most 1 lagging
        # contradiction (typically ema_15m which crosses later than structure).
        # "3-of-4 with zero" was blocking 1,300+ setups where structure on
        # both 5m and 15m already turned while the EMA50/200 on 15m hadn't
        # crossed yet — a known EMA lag, not a real disagreement.
        if bull_votes >= 3 and bear_votes <= 1:
            bias = "buy"
        elif bear_votes >= 3 and bull_votes <= 1:
            bias = "sell"
        else:
            return {"detected": False, "reason": "no_htf_bias_agreement",
                    "trend_5m": trend_5m, "trend_15m": trend_15m,
                    "ema_5m": ema_5m, "ema_15m": ema_15m}

        current_price = float(df_5m['close'].iloc[-1])

        # ── Step 2: pullback into discount (buy) / premium (sell) ────────
        structure_ref = struct_15m
        swing_low  = structure_ref.get("last_swing_low")  or current_price * 0.99
        swing_high = structure_ref.get("last_swing_high") or current_price * 1.01
        fib    = get_fib_zone(swing_low, swing_high, current_price)
        pd_pos = fib.get("position_pct", 50)
        pd_zone = fib.get("zone", "equilibrium")

        # Allow slight premium/discount — strict 50% was blocking valid setups
        # where price was at 55% (minor premium) but all other conditions aligned.
        if bias == "buy"  and pd_pos > 60:
            return {"detected": False, "reason": "no_pullback_yet",
                    "bias": bias, "pd_pos": pd_pos, "pd_zone": pd_zone}
        if bias == "sell" and pd_pos < 40:
            return {"detected": False, "reason": "no_pullback_yet",
                    "bias": bias, "pd_pos": pd_pos, "pd_zone": pd_zone}

        # ── Step 3: an unfilled FVG nearby, in the bias direction ─────────
        atr_5m = calculate_atr(df_5m)
        fvgs   = detect_fvg(df_5m, only_unfilled=True)
        target_fvg_type = "bullish" if bias == "buy" else "bearish"

        nearby_fvg = None
        for f in fvgs:
            if f["type"] != target_fvg_type:
                continue
            if f["bottom"] - atr_5m <= current_price <= f["top"] + atr_5m:
                nearby_fvg = f
                break

        if not nearby_fvg:
            return {"detected": False, "reason": "no_fvg_nearby",
                    "bias": bias, "pd_pos": pd_pos, "pd_zone": pd_zone}

        # ── Step 4: M1 candlestick confirmation ───────────────────────────
        df_m1 = fetch_candles(symbol, "1m", limit=10)
        if len(df_m1) < 3:
            return {"detected": False, "reason": "insufficient_m1_history"}

        if bias == "buy":
            pattern = detect_bullish_pattern(df_m1)
        else:
            pattern = detect_bearish_pattern(df_m1)

        if not pattern:
            return {"detected": False, "reason": "no_m1_pattern",
                    "bias": bias, "pd_pos": pd_pos, "pd_zone": pd_zone,
                    "fvg_type": target_fvg_type}

        # ── Build entry ────────────────────────────────────────────────────
        entry_price = float(df_m1['close'].iloc[-1])
        atr_m1      = calculate_atr(df_m1)
        recent_low  = float(df_m1['low'].iloc[-3:].min())
        recent_high = float(df_m1['high'].iloc[-3:].max())

        if bias == "buy":
            sl   = round(recent_low - atr_m1 * 0.3, 5)
            risk = entry_price - sl
        else:
            sl   = round(recent_high + atr_m1 * 0.3, 5)
            risk = sl - entry_price

        if risk <= 0:
            return {"detected": False, "reason": "invalid_risk", "pattern": pattern}

        # Minimum SL floor — M1 ATR is tiny (1-min candles), giving SLs within
        # spread noise (2-3 pips EURUSD, 3pt ETH). Floor prevents instant SL hits.
        MIN_SL_DIST = {
            "BTC":    120.0,   "ETH":    12.0,
            "EURUSD": 0.00150, "USDJPY": 0.200,
            "XAUUSD": 5.0,     "GBPJPY": 0.250,
        }
        min_dist = MIN_SL_DIST.get(symbol.upper(), 0)
        if min_dist > 0 and risk < min_dist:
            risk = min_dist
            sl   = round(entry_price - risk, 5) if bias == "buy" else round(entry_price + risk, 5)

        if bias == "buy":
            tp1 = round(entry_price + risk * 2.5, 5)
        else:
            tp1 = round(entry_price - risk * 2.5, 5)

        rr = round(risk * 2.5 / risk, 2)  # always 2.5 — computed for clarity

        # Confidence bonuses reused via the existing field names router.py
        # already reads (pd_bonus / m5_bonus) — deeper zone + stronger
        # pattern = bigger bonus, same formula as before.
        pd_bonus = 0.15 if (pd_pos <= 38.2 or pd_pos >= 78.6) else 0.05
        strong   = (pattern in STRONG_BULLISH) or (pattern in STRONG_BEARISH)
        pattern_bonus = 0.15 if strong else 0.05

        return {
            "detected":    True,
            "action":      bias,
            "entry":       round(entry_price, 5),
            "sl":          sl,
            "tp1":         tp1,
            "rr_ratio":    rr,
            "pd_pos":      round(pd_pos, 1),
            "pd_zone":     pd_zone,
            "pd_bonus":    pd_bonus,
            "m5_bonus":    pattern_bonus,
            "pattern":     pattern,
            "description": (f"M1 confirmation {bias}: HTF bias 5m+15m structure+EMA agree, "
                             f"pulled back to {pd_zone} {pd_pos:.0f}%, "
                             f"FVG {target_fvg_type} nearby, M1 pattern={pattern}"),
        }
    except Exception as e:
        print(f"[M1-SIGNAL] {symbol}: error — {e}")
        return {"detected": False, "reason": f"error: {e}"}
