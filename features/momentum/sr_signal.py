"""
Support/Resistance signal — mean-reversion, for ranging markets.

The M1 momentum strategy (m1_signal.py) assumes a trending market: strong
recent movement continues. In a RANGING market that assumption is backwards
— a strong move within a tight range is usually the leg that's about to
reverse, not continue. That's exactly what happened to 3 straight ETH
SELLs on 2026-06-27: M1+M5 agreed bearish each time, but ETH was bouncing
in a 1574-1583 band the whole session, so each "strong bearish move" was
really just the range reaching its bottom edge before bouncing back up.

This is the opposite-regime tool: buy near support, sell near resistance.
Simple by design — the lookback range itself defines the levels, no
swing-point confirmation lag, no momentum direction needed at all.

Three patterns, checked in priority order — each answers a different
question about how price is behaving AT the level:

  1. FAKE BREAKOUT (reversal)    — price poked beyond the level and
     snapped back. The failed breakout itself confirms the level is
     respected; this is the strongest reversal signal of the three.
  2. BREAK + RETEST (continuation) — price genuinely closed beyond the
     level, held there, then pulled back to retest it from the other
     side. If it holds, the level just flipped roles (old resistance =
     new support) and price should continue in the breakout direction —
     this is the ONE case where trading WITH the move, not against it,
     is correct for this strategy.
  3. Plain near-edge (mean-reversion) — the original logic: price is
     simply approaching a tested level with no break attempt either way.
"""


def _level_touches(df, level: float, tol: float, is_resistance: bool) -> int:
    if is_resistance:
        return int((df['high'].iloc[:-1] >= level - tol).sum())
    return int((df['low'].iloc[:-1] <= level + tol).sum())


def _detect_fake_breakout(df, atr: float, support: float, resistance: float,
                           lookback_candles: int = 5):
    """
    Checks the last `lookback_candles` (excluding the current forming one)
    for a level breach that has since snapped back inside — a failed
    breakout. Returns {"action", "broke_level", "note"} or None.
    """
    if len(df) < lookback_candles + 2:
        return None
    recent        = df.iloc[-(lookback_candles + 1):-1]
    current_close = float(df['close'].iloc[-1])

    broke_above = recent[recent['high'] > resistance]
    if not broke_above.empty and current_close < resistance:
        wick_high = float(broke_above['high'].max())
        return {"action": "sell", "broke_level": resistance, "wick": wick_high,
                "note": f"fake breakout above resistance {resistance:.5f} "
                        f"(wicked to {wick_high:.5f}, snapped back)"}

    broke_below = recent[recent['low'] < support]
    if not broke_below.empty and current_close > support:
        wick_low = float(broke_below['low'].min())
        return {"action": "buy", "broke_level": support, "wick": wick_low,
                "note": f"fake breakout below support {support:.5f} "
                        f"(wicked to {wick_low:.5f}, snapped back)"}

    return None


def _detect_break_retest(df, atr: float, support: float, resistance: float,
                          lookback_candles: int = 15, retest_tol_atr: float = 0.3):
    """
    Checks if price genuinely CLOSED beyond the range within the last
    `lookback_candles`, and is now pulling back to retest that broken
    level from the other side. Returns {"action", "level", "note"} or None.
    """
    if len(df) < lookback_candles + 2:
        return None
    current_price = float(df['close'].iloc[-1])
    recent        = df.iloc[-(lookback_candles + 1):-1]
    tol           = retest_tol_atr * atr
    conviction     = atr * 0.2

    broke_up = recent[recent['close'] > resistance + conviction]
    if not broke_up.empty:
        # Retesting the old resistance from above (now acting as support):
        # price must be close to it AND not have closed back below it.
        if abs(current_price - resistance) <= tol and current_price >= resistance - tol * 0.3:
            return {"action": "buy", "level": resistance,
                    "note": f"break+retest of old resistance {resistance:.5f} "
                            f"(now support) — continuation long"}

    broke_down = recent[recent['close'] < support - conviction]
    if not broke_down.empty:
        if abs(current_price - support) <= tol and current_price <= support + tol * 0.3:
            return {"action": "sell", "level": support,
                    "note": f"break+retest of old support {support:.5f} "
                            f"(now resistance) — continuation short"}

    return None


def get_support_resistance_signal(
    symbol:        str,
    lookback:      int   = 50,
    proximity_atr: float = 0.7,
    min_range_atr: float = 2.0,
) -> dict:
    """
    Returns {"detected": False} or a dict with action/entry/sl/tp1/rr_ratio.
    min_range_atr guards against firing on a range that's too tight to be
    meaningful — if the whole range is barely wider than ATR, "support"
    and "resistance" are just noise, not real levels.
    """
    from features.market.fetcher import fetch_candles
    from features.smc.engine import calculate_atr

    try:
        df = fetch_candles(symbol, "15m", limit=lookback)
        if len(df) < lookback:
            return {"detected": False, "reason": "insufficient_history"}

        atr = calculate_atr(df)
        if atr <= 0:
            return {"detected": False, "reason": "zero_atr"}

        current_price = float(df['close'].iloc[-1])
        # Exclude the current (forming) candle from the range so it can't
        # self-reference its own high/low as the level it's "near."
        resistance = float(df['high'].iloc[:-1].max())
        support    = float(df['low'].iloc[:-1].min())
        range_size = resistance - support
        range_atr  = round(range_size / atr, 2) if atr > 0 else 0

        if range_size < atr * min_range_atr:
            return {"detected": False, "reason": "range_too_tight",
                    "range_atr": range_atr, "support": round(support, 5),
                    "resistance": round(resistance, 5)}

        target_rr = 2.5

        # ── Pattern 1: fake breakout — fade the failed break ──────────────
        fake = _detect_fake_breakout(df, atr, support, resistance)
        if fake:
            action = fake["action"]
            if action == "buy":
                sl   = round(fake["wick"] - atr * 0.3, 5)
                risk = current_price - sl
                tp1  = round(min(current_price + risk * target_rr, resistance - atr * 0.2), 5)
            else:
                sl   = round(fake["wick"] + atr * 0.3, 5)
                risk = sl - current_price
                tp1  = round(max(current_price - risk * target_rr, support + atr * 0.2), 5)
            reward = abs(tp1 - current_price)
            risk   = abs(current_price - sl)
            if risk > 0 and reward > 0:
                return {
                    "detected": True, "action": action,
                    "entry": round(current_price, 5), "sl": sl, "tp1": tp1,
                    "rr_ratio": round(reward / risk, 2), "atr": round(atr, 5),
                    "support": round(support, 5), "resistance": round(resistance, 5),
                    "range_atr": range_atr, "pattern": "fake_breakout",
                    "description": f"S/R {action} — {fake['note']}",
                }

        # ── Pattern 2: break + retest — continuation, not reversion ──────
        retest = _detect_break_retest(df, atr, support, resistance)
        if retest:
            action = retest["action"]
            level  = retest["level"]
            # No opposite-edge cap here — we're trading INTO new territory
            # beyond the old range, not fading back across it.
            if action == "buy":
                sl   = round(level - atr * 0.4, 5)
                risk = current_price - sl
                tp1  = round(current_price + risk * target_rr, 5)
            else:
                sl   = round(level + atr * 0.4, 5)
                risk = sl - current_price
                tp1  = round(current_price - risk * target_rr, 5)
            reward = abs(tp1 - current_price)
            risk   = abs(current_price - sl)
            if risk > 0 and reward > 0:
                return {
                    "detected": True, "action": action,
                    "entry": round(current_price, 5), "sl": sl, "tp1": tp1,
                    "rr_ratio": round(reward / risk, 2), "atr": round(atr, 5),
                    "support": round(support, 5), "resistance": round(resistance, 5),
                    "range_atr": range_atr, "pattern": "break_retest",
                    "description": f"S/R {action} — {retest['note']}",
                }

        # ── Pattern 3: plain near-edge mean-reversion ─────────────────────
        # abs() matters here: the old one-sided checks (current-support,
        # resistance-current) stay <= proximity even when price is FAR
        # beyond the level — e.g. resistance-current_price for a price
        # well above resistance is a large NEGATIVE number, which still
        # satisfies "<= proximity". That let a real breakout get treated
        # as "near resistance, sell" instead of recognizing price had
        # already broken through. abs() requires price to genuinely be
        # close to the level on either side, not just anywhere past it.
        proximity       = proximity_atr * atr
        near_support    = abs(current_price - support)    <= proximity
        near_resistance = abs(current_price - resistance) <= proximity
        dist_to_support    = round((current_price - support) / atr, 2)
        dist_to_resistance = round((resistance - current_price) / atr, 2)

        if near_support == near_resistance:
            return {"detected": False, "reason": "not_near_edge",
                    "range_atr": range_atr,
                    "dist_to_support_atr": dist_to_support,
                    "dist_to_resistance_atr": dist_to_resistance}

        # Validate the level has actually been TESTED before — without
        # this, resistance/support is just the single highest/lowest
        # candle in a sliding window, and during a genuine uptrend the
        # most recent high IS that rolling max almost by definition. A
        # real level has been approached and rejected more than once.
        touch_tol   = atr * 0.5
        min_touches = 2
        if near_resistance:
            touches = _level_touches(df, resistance, touch_tol, is_resistance=True)
            if touches < min_touches:
                return {"detected": False, "reason": "resistance_untested",
                        "touches": touches, "range_atr": range_atr}
        else:
            touches = _level_touches(df, support, touch_tol, is_resistance=False)
            if touches < min_touches:
                return {"detected": False, "reason": "support_untested",
                        "touches": touches, "range_atr": range_atr}

        if near_support:
            action = "buy"
            sl   = round(support - atr * 0.5, 5)
            risk = current_price - sl
            tp1  = round(min(current_price + risk * target_rr, resistance - atr * 0.2), 5)
        else:
            action = "sell"
            sl   = round(resistance + atr * 0.5, 5)
            risk = sl - current_price
            tp1  = round(max(current_price - risk * target_rr, support + atr * 0.2), 5)

        risk   = abs(current_price - sl)
        reward = abs(tp1 - current_price)
        if risk <= 0 or reward <= 0:
            return {"detected": False, "reason": "invalid_risk_reward"}

        return {
            "detected":    True,
            "action":      action,
            "entry":       round(current_price, 5),
            "sl":          sl,
            "tp1":         tp1,
            "rr_ratio":    round(reward / risk, 2),
            "atr":         round(atr, 5),
            "support":     round(support, 5),
            "resistance":  round(resistance, 5),
            "range_atr":   round(range_size / atr, 2),
            "touches":     touches,
            "pattern":     "near_edge",
            "description": (f"S/R {action} (price near "
                             f"{'support' if near_support else 'resistance'}, "
                             f"tested {touches}x, "
                             f"range {support:.5f}-{resistance:.5f}, "
                             f"{round(range_size / atr, 1)}xATR wide)"),
        }
    except Exception as e:
        print(f"[SR-SIGNAL] {symbol}: error — {e}")
        return {"detected": False, "reason": f"error: {e}"}
