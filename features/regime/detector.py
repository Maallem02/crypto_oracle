"""
Market regime detector — "should we even be trading this symbol right now?"

Not another direction predictor — a gate that applies ACROSS all three
strategies (lg_primary, support_resistance, m1_confirmation). Built after
a recurring pattern across nearly every big loss reviewed this session:
structure, CHoCH, EMA, and momentum all agreed on a direction and the
market did something else anyway. That's not one broken signal — it's
that every confirmation layer is built from the same kind of recent-price
data, so they fail TOGETHER in exactly the same conditions: a market
that's not really trending, and has been whipsawing between HTF
agreement and conflict recently.

Three regimes:
  trending — ADX healthy, conditions are clear. No change to any strategy.
  ranging  — ADX low but stable, no recent whipsaw. Fine — this is what
             support_resistance is built for. No change.
  choppy   — ADX low AND recent HTF-conflict whipsaw. The condition behind
             nearly every big loss this session. Raises the score bar for
             ALL strategies on this symbol — doesn't block trading outright
             (a hard block risks missing a genuinely strong setup that
             happens to land during a noisy ADX reading), just requires
             more conviction to get through.
"""
from datetime import datetime, timedelta


def get_market_regime(
    symbol:               str,
    adx_threshold:        float = 20.0,
    conflict_window_hours: float = 2.0,
    conflict_threshold:    int   = 3,
) -> dict:
    """
    Returns {"regime": "trending"|"ranging"|"choppy", "adx_1h": float,
    "recent_conflicts": int}.
    """
    from features.market.fetcher import fetch_candles
    from features.smc.engine import calculate_adx
    from features.trading.data_collector import load_rejections

    try:
        df_1h = fetch_candles(symbol, "1h", limit=50)
        adx = calculate_adx(df_1h)
        if adx != adx:   # NaN check
            adx = adx_threshold
    except Exception as e:
        print(f"[REGIME] {symbol}: ADX error — {e}")
        adx = adx_threshold

    cutoff = datetime.now() - timedelta(hours=conflict_window_hours)
    recent_count = 0
    try:
        recent = load_rejections(limit=500, symbol=symbol, reason_contains="htf_conflict_confirmed")
        for r in recent:
            ts = r.get("ts")
            if not ts:
                continue
            try:
                if datetime.fromisoformat(ts) >= cutoff:
                    recent_count += 1
            except Exception:
                continue
    except Exception as e:
        print(f"[REGIME] {symbol}: conflict-count error — {e}")

    is_low_adx     = adx < adx_threshold
    is_whipsawing  = recent_count >= conflict_threshold

    if is_low_adx and is_whipsawing:
        regime = "choppy"
    elif is_low_adx:
        regime = "ranging"
    else:
        regime = "trending"

    return {
        "regime":           regime,
        "adx_1h":           round(float(adx), 1),
        "recent_conflicts": recent_count,
    }
