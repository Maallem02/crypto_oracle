"""
Price-action strategy — trend pullback + break & retest.

Deliberately indicator-free: trend, levels and confirmation all come from
price itself. Two setups only:

  A. TREND PULLBACK  strong HTF trend -> pullback into a level -> rejection
                     candle -> enter with the trend
  B. BREAK & RETEST  level breaks -> price returns to it -> the old level
                     holds as the opposite -> enter in the break direction

Range trading (buy support / sell resistance inside a range) is NOT included:
that is exactly what features/momentum/sr_signal.py did, and it was disabled
after measuring 31% WR / -$51 over 200 real trades.

Every function here is PURE — it takes DataFrames and returns a dict. Nothing
fetches, nothing touches MT5. That is what makes it replayable bar-by-bar in
the backtest without a live connection.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ── candle anatomy ───────────────────────────────────────────────────────────
def _atr(df: pd.DataFrame, period: int = 14) -> float:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    v = tr.rolling(period).mean().iloc[-1]
    return float(v) if not np.isnan(v) else float((df["high"] - df["low"]).mean())


def rejection_candle(df: pd.DataFrame, direction: str, i: int = -1) -> str | None:
    """
    Confirmation candle at index i. Returns the pattern name or None.

    Bullish: hammer / pin bar (long lower wick) or bullish engulfing.
    Bearish: shooting star (long upper wick) or bearish engulfing.
    Thresholds match features/momentum/candlestick_patterns.py so the two
    modules cannot disagree about what a pin bar is.
    """
    o, h, l, c = (float(df["open"].iloc[i]), float(df["high"].iloc[i]),
                  float(df["low"].iloc[i]),  float(df["close"].iloc[i]))
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l

    # Pin bar measured against the RANGE, not the body. The inherited
    # candlestick_patterns.hammer() test is `upper <= body * 0.5`, which is
    # unsatisfiable for the textbook pin bar: a tiny body makes body*0.5 tiny,
    # so any upper wick at all fails it. A 0.05 body with a 4.0 rejection wick
    # — the cleanest possible pin — was being rejected.
    if direction == "bullish":
        if lower >= rng * 0.60 and body <= rng * 0.35 and upper <= rng * 0.20:
            return "pin_bar"
        po, pc = float(df["open"].iloc[i - 1]), float(df["close"].iloc[i - 1])
        if pc < po and c > o and o <= pc and c >= po:
            return "bullish_engulfing"
    else:
        if upper >= rng * 0.60 and body <= rng * 0.35 and lower <= rng * 0.20:
            return "pin_bar"
        po, pc = float(df["open"].iloc[i - 1]), float(df["close"].iloc[i - 1])
        if pc > po and c < o and o >= pc and c <= po:
            return "bearish_engulfing"
    return None


def _distinct_swings(idx: np.ndarray, values: np.ndarray, lookback: int,
                     want_max: bool) -> np.ndarray:
    """
    Collapse runs of adjacent swing flags into one point per swing.

    detect_swings uses `>=` / `<=` so that double tops count, but that also
    flags EVERY bar of a flat plateau. The last two "swings" were then often
    two neighbouring bars of the same plateau with equal values, so
    "higher high" could never be true and every trend read as a range.
    Points closer than `lookback` bars belong to the same swing; keep the
    most extreme one.
    """
    if len(idx) == 0:
        return idx
    groups, cur = [], [idx[0]]
    for k in idx[1:]:
        if k - cur[-1] <= lookback:
            cur.append(k)
        else:
            groups.append(cur); cur = [k]
    groups.append(cur)
    pick = np.argmax if want_max else np.argmin
    return np.array([g[int(pick(values[g]))] for g in groups])


# ── step 1: trend ────────────────────────────────────────────────────────────
def htf_trend(df_htf: pd.DataFrame, lookback: int = 5) -> str:
    """
    Higher-timeframe trend from swing structure only — HH+HL = uptrend,
    LH+LL = downtrend, anything else = range. No moving averages.
    """
    w = lookback * 2 + 1
    hi = df_htf["high"].values
    lo = df_htf["low"].values
    sh = np.flatnonzero(np.nan_to_num(
        hi >= pd.Series(hi).rolling(w, center=True).max().values, nan=0).astype(bool))
    sl = np.flatnonzero(np.nan_to_num(
        lo <= pd.Series(lo).rolling(w, center=True).min().values, nan=0).astype(bool))
    sh = _distinct_swings(sh, hi, lookback, want_max=True)
    sl = _distinct_swings(sl, lo, lookback, want_max=False)
    if len(sh) < 2 or len(sl) < 2:
        return "range"

    # A higher high must be MEANINGFULLY higher. Without a tolerance, an
    # oscillation whose peaks differ by a rounding error reads as a clean
    # uptrend — which is the "entering in the middle of a range" mistake,
    # arrived at by arithmetic instead of by eye.
    tol = _atr(df_htf) * 0.10
    hh = hi[sh[-1]] > hi[sh[-2]] + tol
    hl = lo[sl[-1]] > lo[sl[-2]] + tol
    lh = hi[sh[-1]] < hi[sh[-2]] - tol
    ll = lo[sl[-1]] < lo[sl[-2]] - tol
    if hh and hl:
        return "uptrend"
    if lh and ll:
        return "downtrend"
    return "range"


# ── step 2: levels ───────────────────────────────────────────────────────────
def key_levels(df: pd.DataFrame, atr: float, lookback: int = 5,
               min_touches: int = 2, max_levels: int = 5) -> dict:
    """
    Support/resistance from clustered swing points — "areas where price has
    reacted several times", not exact prices. Swings within 0.5xATR are merged
    into one level and the touch count is what ranks them.

    Returns {"support": [...], "resistance": [...]} sorted by strength,
    capped at max_levels each (the brief says 2-5 levels, not dozens).
    """
    w = lookback * 2 + 1
    hi, lo = df["high"].values, df["low"].values
    sh_i = np.flatnonzero(np.nan_to_num(
        hi >= pd.Series(hi).rolling(w, center=True).max().values, nan=0).astype(bool))
    sl_i = np.flatnonzero(np.nan_to_num(
        lo <= pd.Series(lo).rolling(w, center=True).min().values, nan=0).astype(bool))

    def cluster(vals):
        if len(vals) == 0:
            return []
        out = []
        for v in sorted(vals):
            if out and abs(v - out[-1][0]) <= atr * 0.5:
                n = out[-1][1] + 1
                out[-1] = ((out[-1][0] * out[-1][1] + v) / n, n)   # running mean
            else:
                out.append((v, 1))
        return [(price, touches) for price, touches in out if touches >= min_touches]

    res = sorted(cluster(hi[sh_i]), key=lambda x: -x[1])[:max_levels]
    sup = sorted(cluster(lo[sl_i]), key=lambda x: -x[1])[:max_levels]
    return {"resistance": res, "support": sup}


# ── the two setups ───────────────────────────────────────────────────────────
def _nearest(levels: list, price: float, atr: float, tol_atr: float):
    """Closest level within tol_atr of price, or None."""
    cands = [(abs(price - p), p, t) for p, t in levels if abs(price - p) <= atr * tol_atr]
    if not cands:
        return None
    _, p, t = min(cands)
    return p, t


def price_action_signal(
    df: pd.DataFrame,                 # trading timeframe, most recent bar LAST
    df_htf: pd.DataFrame,             # higher timeframe for the trend
    tol_atr: float = 0.6,             # how close to a level counts as "at" it
    min_rr: float = 2.0,              # step 7 of the brief
    sl_buffer_atr: float = 0.25,      # stop sits BEYOND the level, not on it
    require_trend: bool = True,       # step 1: only trade with the trend
    min_stop_atr: float = 0.50,       # reject setups whose stop is tighter than this
) -> dict:
    """
    Returns {"detected": bool, "reason": str, ...}. When detected, also
    action / entry / sl / tp1 / rr_ratio / setup / pattern, matching the shape
    the router's _try_fallback() already expects.
    """
    if len(df) < 60 or len(df_htf) < 30:
        return {"detected": False, "reason": "not_enough_bars"}

    atr = _atr(df)
    if atr <= 0:
        return {"detected": False, "reason": "atr_zero"}

    price = float(df["close"].iloc[-1])
    trend = htf_trend(df_htf)
    levels = key_levels(df, atr)

    # ── Step 1 — direction is decided by the HTF trend, not by the candle ──
    if trend == "uptrend":
        direction, want = "buy", "bullish"
    elif trend == "downtrend":
        direction, want = "sell", "bearish"
    else:
        if require_trend:
            return {"detected": False, "reason": "htf_range_no_trend", "trend": trend}
        return {"detected": False, "reason": "htf_range_no_trend", "trend": trend}

    # ── Step 4 — confirmation candle must be present and agree ─────────────
    pattern = rejection_candle(df, want)
    if not pattern:
        return {"detected": False, "reason": "no_confirmation_candle",
                "trend": trend, "bias": direction}

    # ── Step 3 — price must actually BE at a level ─────────────────────────
    #  A. trend pullback : buy at support in an uptrend / sell at resistance
    #  B. break & retest : buy at broken RESISTANCE now acting as support
    setup = level = touches = None
    if direction == "buy":
        near_sup = _nearest(levels["support"], price, atr, tol_atr)
        if near_sup:
            setup, (level, touches) = "trend_pullback", near_sup
        else:
            near_res = _nearest(levels["resistance"], price, atr, tol_atr)
            # a retest only counts if price already CLOSED above that resistance
            if near_res and float(df["close"].iloc[-6:].max()) > near_res[0] + atr * 0.1:
                setup, (level, touches) = "break_retest", near_res
    else:
        near_res = _nearest(levels["resistance"], price, atr, tol_atr)
        if near_res:
            setup, (level, touches) = "trend_pullback", near_res
        else:
            near_sup = _nearest(levels["support"], price, atr, tol_atr)
            if near_sup and float(df["close"].iloc[-6:].min()) < near_sup[0] - atr * 0.1:
                setup, (level, touches) = "break_retest", near_sup

    if setup is None:
        return {"detected": False, "reason": "not_at_a_level",
                "trend": trend, "bias": direction, "pattern": pattern}

    # ── Step 5 — stop goes where the idea is WRONG: beyond the level ───────
    # Anchored to the confirmation candle's extreme when that sits further out,
    # so a long-wicked pin bar isn't stopped by its own wick.
    if direction == "buy":
        sl = min(level, float(df["low"].iloc[-1])) - atr * sl_buffer_atr
        risk = price - sl
    else:
        sl = max(level, float(df["high"].iloc[-1])) + atr * sl_buffer_atr
        risk = sl - price
    if risk <= 0:
        return {"detected": False, "reason": "invalid_stop"}

    # ── Plancher de distance au stop (2026-08-09) ─────────────────────────
    # Quand le prix entre PILE sur le niveau, la distance au stop se réduit au
    # seul tampon de 0.25xATR. Ces setups ne survivent pas au bruit ordinaire.
    #
    # Backtest, expectancy par largeur de stop :
    #   < 0.50xATR :  n=8    -1.0000R   <- 8 pertes sur 8, toutes au stop plein
    #   0.50-0.70  :  n=83   +0.2035R
    #   0.70-0.90  :  n=222  +0.1546R
    #   0.90-1.20  :  n=216  +0.0840R
    #   > 1.20     :  n=226  +0.1013R
    #
    # En live, deux ETH le 08-09 à 0.27xATR : biais CORRECT (le prix est monté
    # de ~5R ensuite) mais le stop de 1.11 point sur un ATR de 4.1 a été
    # balayé par l'oscillation normale. Avec les 8 du backtest : 0 gain sur 10.
    #
    # On REJETTE au lieu d'élargir : élargir ne les sauve pas non plus
    # (-0.712R avec un plancher à 0.75xATR, -0.396R à 1.0xATR). Ce ne sont pas
    # de bons setups mal stoppés, ce sont de mauvais setups. Le rejet ne touche
    # que ~1% des signaux et laisse tout le reste intact.
    if risk < atr * min_stop_atr:
        return {"detected": False,
                "reason": f"stop_too_tight_{risk/atr:.2f}xATR",
                "trend": trend, "bias": direction, "pattern": pattern}

    # ── Steps 6/7 — target at least min_rr, measured to the next level ─────
    # Prefer a real level as the target; fall back to the fixed R multiple.
    if direction == "buy":
        ahead = [p for p, _ in levels["resistance"] if p > price + risk * min_rr]
        tp1 = min(ahead) if ahead else price + risk * min_rr
    else:
        ahead = [p for p, _ in levels["support"] if p < price - risk * min_rr]
        tp1 = max(ahead) if ahead else price - risk * min_rr
    rr = abs(tp1 - price) / risk
    if rr < min_rr:
        return {"detected": False, "reason": f"rr_too_low_{rr:.2f}"}

    return {
        "detected": True,
        "action": direction,
        "entry": round(price, 5),
        "sl": round(sl, 5),
        "tp1": round(tp1, 5),
        "rr_ratio": round(rr, 2),
        "atr": round(atr, 5),
        "setup": setup,
        "pattern": pattern,
        "trend": trend,
        "level": round(level, 5),
        "level_touches": touches,
        "description": f"{setup} {direction} @ {level:.5f} ({pattern}, "
                       f"{touches} touches, {trend}) RR {rr:.1f}",
    }


# ── live wrapper ─────────────────────────────────────────────────────────────
# Measured defaults, backtest 2026-08-01 (755 signals, M15 + H4, live exit
# policy BE1.5R + trail40%):
#
#   raw                        n=755  avg_R +0.1116   train +0.139 / test +0.086
#   - XAGUSD                   n=570  avg_R +0.1552   train +0.202 / test +0.112
#   - XAGUSD, touches<=3       n=498  avg_R +0.1798   train +0.214 / test +0.147
#
# MAX_LEVEL_TOUCHES exists because the data says the opposite of the folklore:
# expectancy falls monotonically with touch count (+0.162 / +0.052 / +0.002 /
# -0.112 / -0.508 for 2/3/4/5/6 touches). A level hit many times is being worn
# down, not defended. The monotonicity across six buckets is what makes this
# credible rather than a cherry-picked bucket.
#
# XAGUSD is excluded for the same reason it loses under every other strategy
# tested this session (-0.0226 here).
# ── Liste BLANCHE de symboles (2026-08-13) ──────────────────────────────────
# Passage d'une liste d'exclusion à une liste d'autorisation, sur décision
# utilisateur : price action ne tourne plus que sur les métaux.
#
# Comparaison PA vs LG sur la même fenêtre (2026-08-03 -> 08-13), en R :
#   symbole   PA                     LG
#   BTC       4t   0% WR  -1.05R     18t 39% WR  -0.11R   <- PA: 4 pertes sur 4
#   ETH       7t  43% WR  -0.19R     10t 40% WR  -0.16R
#   EURUSD    1t   0% WR  -1.00R      8t 38% WR  -0.23R
#   XAUUSD    7t  43% WR  +0.18R     12t 42% WR  +0.31R
#   XAGUSD    aucun trade PA         10t 30% WR  +0.51R
#
# BTC/ETH/EURUSD : PA perd sur les trois et n'apporte rien que LG ne fasse
# mieux. XAUUSD est le seul symbole où PA est positif.
#
# RÉSERVE sur XAGUSD : il était exclu parce que le BACKTEST mesurait -0.0226R
# pour PA sur l'argent, et il n'existe aucun trade PA live dessus. Le +0.51R
# de l'argent vient de LG, pas de PA. Réactivé sur demande explicite — à
# surveiller comme une hypothèse, pas comme un réglage validé.
PA_ALLOWED_SYMBOLS = {"XAUUSD", "XAGUSD"}
MAX_LEVEL_TOUCHES  = 3


def get_price_action_signal(symbol: str, tf: str = "15m", htf: str = "4h") -> dict:
    """
    Live entry point — fetches candles and applies the measured filters.
    Returns the same dict shape the router's _try_fallback() consumes.
    """
    if symbol.upper() not in PA_ALLOWED_SYMBOLS:
        return {"detected": False, "reason": "pa_symbol_not_allowed"}
    try:
        from features.market.fetcher import fetch_candles
        df     = fetch_candles(symbol, tf,  limit=151)
        df_htf = fetch_candles(symbol, htf, limit=61)
    except Exception as e:
        return {"detected": False, "reason": f"fetch_error: {e}"}

    # Drop the bar that is still forming. copy_rates_from_pos() returns the
    # CURRENT candle as the last row, so without this the confirmation test
    # runs on an incomplete candle — and a pin bar is not a pin bar until it
    # closes. A bar can look like a clean rejection five minutes in and end
    # up a full-bodied trend candle. The backtest scored closed bars only,
    # so evaluating the live forming bar was measuring something else.
    if len(df) > 1:
        df = df.iloc[:-1]
    if len(df_htf) > 1:
        df_htf = df_htf.iloc[:-1]

    sig = price_action_signal(df, df_htf)
    if sig.get("detected") and sig.get("level_touches", 99) > MAX_LEVEL_TOUCHES:
        return {"detected": False,
                "reason": f"level_too_worn_{sig['level_touches']}_touches"}
    return sig
