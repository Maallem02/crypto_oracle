"""
Zone scorer — runtime integration with the trading engine.

Called once per signal in router.py:
    from features.zones.scorer import get_zone_boost
    zone_boost = get_zone_boost(symbol, entry_price, bias, analysis)

Returns a float added to blended_score:
  +15  strong zone: ML prob ≥ 0.72 OR historical bounce_rate ≥ 0.65 (≥3 touches)
  +10  good zone:   ML prob ≥ 0.62
  +5   moderate:    ML prob ≥ 0.52
   0   neutral:     no nearby zone / no model / no history
  -10  zone broken or historically weak (< 30% bounce rate with ≥ 3 touches)

Resources are loaded lazily on first call and cached in module scope.
"""

import numpy as np
from datetime import datetime, timezone

from features.zones.detector import load_zone_database
from features.zones.trainer  import FEATURE_COLS, get_zone_model

# Cache
_db:    dict | None = None
_model              = None

# How close price must be to the zone edge to consider it a "touch"
PROXIMITY_ATR = 1.5   # within 1.5 × ATR of the zone boundary


def _load_resources():
    global _db, _model
    if _db is None:
        _db = load_zone_database()
        n = sum(len(v) for v in _db.values())
        print(f"[ZONE-SCORE] Database loaded: {n} zones across {len(_db)} symbols")
    if _model is None:
        _model = get_zone_model()
        if _model:
            print("[ZONE-SCORE] Zone ML model ready")


def _zone_age_candles(created_at_str: str) -> int:
    """Approximate zone age in H1 candles (1 candle ≈ 1 hour)."""
    try:
        dt = datetime.fromisoformat(str(created_at_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return max(1, int(hours))
    except Exception:
        return 500   # default: moderately old zone


def _predict_prob(zone: dict, current_price: float, bias: str, analysis: dict) -> float:
    """Build feature vector and return bounce probability from zone ML model."""
    global _model
    if _model is None:
        return -1.0   # signal: use statistical fallback

    tc  = zone.get("touch_count",  0)
    bc  = zone.get("bounce_count", 0)
    zl  = zone["price_low"]
    zh  = zone["price_high"]
    z_atr = zone["atr"]

    z_type      = 1 if zone["type"] == "demand" else -1
    age         = _zone_age_candles(zone.get("created_at", ""))
    touch_num   = tc + 1
    prior_br    = bc / max(tc, 1)
    width_atr   = (zh - zl) / max(z_atr, 1e-9)

    rsi         = float(analysis.get("rsi",  50.0))
    atr         = float(analysis.get("atr",  current_price * 0.001))
    atr_ratio   = atr / max(current_price, 1e-9)

    # Approach speed proxy: ADX normalised (strong trend = fast approach)
    adx         = float(analysis.get("adx", 20.0))
    direction   = 1 if bias == "buy" else -1
    approach    = ((adx - 20) / 20.0) * direction   # ∈ [-1, +1] roughly

    # HTF trend from structure
    struct_trend = (analysis.get("structure") or {}).get("trend", "neutral")
    htf_map      = {"bullish": 1, "bearish": -1, "neutral": 0}
    htf          = htf_map.get(struct_trend, 0)

    now      = datetime.now()
    hour     = now.hour
    dow      = now.weekday()
    body_ratio = 0.5   # not directly available in analysis; neutral default

    vec = np.array([[
        z_type, age, touch_num, prior_br, width_atr,
        rsi, approach, htf,
        hour, dow, atr_ratio, body_ratio,
    ]], dtype=float)

    try:
        prob = float(_model.predict_proba(vec)[0][1])
        return round(prob, 3)
    except Exception as e:
        print(f"[ZONE-SCORE] predict error: {e}")
        return -1.0


def _boost_from_prob(prob: float, touch_count: int) -> float:
    """Convert bounce probability to score boost."""
    if prob >= 0.72:
        return 15.0
    if prob >= 0.62:
        return 10.0
    if prob >= 0.52:
        return 5.0
    if prob < 0.38 and touch_count >= 3:
        return -10.0
    return 0.0


def _boost_from_stats(bounce_count: int, touch_count: int) -> float:
    """Statistical fallback when no ML model is available."""
    if touch_count < 2:
        return 0.0
    rate = bounce_count / touch_count
    if rate >= 0.65 and touch_count >= 3:
        return 12.0
    if rate >= 0.50 and touch_count >= 2:
        return 6.0
    if rate < 0.30 and touch_count >= 3:
        return -8.0
    return 0.0


def get_zone_boost(
    symbol:        str,
    current_price: float,
    bias:          str,
    analysis:      dict,
) -> float:
    """
    Main runtime function called by router.py for every signal candidate.

    Finds the nearest historical zone of the correct type, predicts bounce
    probability (ML model or statistical fallback), and returns a score boost.
    """
    try:
        _load_resources()
    except Exception as e:
        print(f"[ZONE-SCORE] resource load error: {e}")
        return 0.0

    zones = _db.get(symbol.upper(), []) if _db else []
    if not zones:
        return 0.0

    target_type = "demand" if bias == "buy" else "supply"
    atr = float(analysis.get("atr", current_price * 0.001))
    if atr <= 0:
        atr = current_price * 0.001

    # ── Find nearby zones of the correct type ───────────────────────────────
    candidates = []
    for z in zones:
        if z.get("broken"):
            continue
        if z["type"] != target_type:
            continue

        zl = z["price_low"]
        zh = z["price_high"]

        # Live break check: the zone's "broken" flag is static from the
        # historical backtest — it does NOT know if price is breaking
        # through THIS zone right now. A demand zone that price has already
        # closed below (or a supply zone closed above) is invalidated live,
        # regardless of what happened to it historically.
        if bias == "buy" and current_price < zl - 0.1 * atr:
            continue
        if bias == "sell" and current_price > zh + 0.1 * atr:
            continue

        if bias == "buy":
            # Price should be at or just above a demand zone (within proximity)
            # Zone entry is at zone_high; we allow price up to PROXIMITY_ATR above it
            dist = current_price - zh   # positive = price above zone top
            if -atr <= dist <= PROXIMITY_ATR * atr:
                candidates.append((abs(dist), z))
        else:
            # Price at or just below a supply zone (zone entry = zone_low)
            dist = zl - current_price
            if -atr <= dist <= PROXIMITY_ATR * atr:
                candidates.append((abs(dist), z))

    if not candidates:
        return 0.0

    # Use the nearest zone
    _, nearest = min(candidates, key=lambda x: x[0])

    tc = nearest.get("touch_count",  0)
    bc = nearest.get("bounce_count", 0)

    # ── ML prediction ────────────────────────────────────────────────────────
    prob = _predict_prob(nearest, current_price, bias, analysis)

    if prob >= 0:
        boost = _boost_from_prob(prob, tc)
        br = bc / max(tc, 1)
        print(f"[ZONE] {symbol} {bias}: nearest {nearest['type']} zone "
              f"[{nearest['price_low']:.5f}–{nearest['price_high']:.5f}] "
              f"touches={tc} bounce_rate={br:.0%} ML_prob={prob:.2f} → {boost:+.0f}pts")
    else:
        boost = _boost_from_stats(bc, tc)
        br = bc / max(tc, 1)
        print(f"[ZONE] {symbol} {bias}: nearest {nearest['type']} zone "
              f"touches={tc} bounce_rate={br:.0%} (stats fallback) → {boost:+.0f}pts")

    return boost


def reload_zone_db():
    """Force reload of zone database from disk (call after re-training)."""
    global _db
    _db = None
    _load_resources()
