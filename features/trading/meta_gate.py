"""
Meta-model gate — ranks the bot's OWN signals.

Not a market predictor. build_lstm.py already tried that on raw M5 sequences
and came back at AUC 0.494-0.534, i.e. a coin flip. This model never sees a
price series directly; it scores a signal the engine has already decided to
take, using what the engine knows at that moment plus the market state around
it, and predicts the R outcome.

Validated (meta_model_study.py, 4 expanding walk-forward folds, Apr->Jul):
    take every signal      : +0.178 .. +0.265 R/trade depending on fold
    take the model's top 10%: +0.190 .. +0.484 R/trade
    mean gain +0.1247R, positive in 4/4 folds

hour and day-of-week are deliberately EXCLUDED. With them the mean gain looked
better (+0.185R) but one fold went negative — classic overfitting to session
patterns in a single quarter. Without them the edge is smaller and holds
everywhere, which is the version worth deploying.

The surviving signal is mostly REGIME, not setup quality: distance from the
EMA200, volatility percentile and efficiency ratio dominate, while LG strength,
CISD and OB/FVG contribute almost nothing — consistent with every other feature
scan run on this system.

IMPORTANT: regime_series() below is the single definition of those features.
The trainer imports it from here rather than reimplementing, so the model can
never be fed differently-computed inputs at runtime than it saw in training.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "meta_model.joblib")

# Canonical feature order. The model is trained on exactly this list, in this
# order; changing it invalidates a persisted model (the trainer stores the list
# alongside the model and load_model() refuses a mismatch).
FEATURE_ORDER = [
    "score", "lg_strength", "wick", "cisd", "choch", "ob", "fvg", "ema50",
    "rsi", "stoch", "pd_pos", "adx", "is_buy", "m4_neutral",
    "er48", "er96", "ext", "atr_ratio", "volpct",
]

# volpct needs a 500-bar rolling window, but the binding constraint is EMA200:
# ewm(span=200) is recursive, so a short window has not converged to the value
# training saw over the full history. At 600 bars the seed still carries ~0.2%
# weight and `ext` differed in the 3rd decimal; at 1200 it is ~6e-6. The extra
# bars cost nothing — fetch_candles caches per (symbol, timeframe).
MIN_BARS   = 1000
FETCH_BARS = 1200

_model = None
_meta: dict = {}


# ── regime features ──────────────────────────────────────────────────────────
def regime_series(high, low, close) -> dict:
    """Full-length arrays of every regime feature. Index into it at bar i."""
    c = np.asarray(close, dtype=float)
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    prev = np.roll(c, 1); prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    atr = pd.Series(tr).rolling(14).mean().bfill().values
    absmove = pd.Series(np.abs(np.diff(c, prepend=c[0])))

    def eff(n):
        net = np.abs(c - np.roll(c, n)); net[:n] = np.nan
        tot = absmove.rolling(n).sum().values
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(tot > 0, net / tot, np.nan)

    return {
        "atr":    atr,
        "er48":   eff(48),
        "er96":   eff(96),
        "ema200": pd.Series(c).ewm(span=200, adjust=False).mean().values,
        "close":  c,
        "atr_s":  pd.Series(tr).rolling(7).mean().bfill().values,
        "atr_l":  pd.Series(tr).rolling(50).mean().bfill().values,
        "volpct": pd.Series(atr).rolling(500).rank(pct=True).values,
    }


def regime_at(s: dict, i: int) -> dict | None:
    """Scalar regime features at bar i, or None if not computable."""
    atr = s["atr"][i]
    if not atr or atr <= 0 or np.isnan(s["er96"][i]) or np.isnan(s["volpct"][i]):
        return None
    return {
        "er48":      float(s["er48"][i]),
        "er96":      float(s["er96"][i]),
        "ext":       float(abs(s["close"][i] - s["ema200"][i]) / atr),
        "atr_ratio": float(s["atr_s"][i] / s["atr_l"][i]) if s["atr_l"][i] else np.nan,
        "volpct":    float(s["volpct"][i]),
    }


def signal_features(analysis: dict, bias: str, macro4h: str) -> dict:
    """The engine-side half of the vector, read off the live analysis dict."""
    lg = analysis.get("liquidity_grab") or {}
    pd_ = analysis.get("premium_discount") or {}
    conf = analysis.get("smc_confluence")
    cisd = analysis.get("cisd") or {}
    struct_trend = (analysis.get("structure") or {}).get("trend")
    price = analysis.get("current_price") or 0
    ema50 = analysis.get("ema50") or 0
    return {
        "score":       float(analysis.get("scalping_score") or 0),
        "lg_strength": float(lg.get("strength") or 0),
        "wick":        1 if lg.get("pattern") == "wick_rejection" else 0,
        "cisd":        1 if cisd.get("detected") else 0,
        "choch":       1 if struct_trend and struct_trend == ("bullish" if bias == "buy" else "bearish") else 0,
        "ob":          1 if conf == "order_block" else 0,
        "fvg":         1 if conf == "fvg" else 0,
        "ema50":       1 if ((bias == "buy" and price >= ema50) or
                             (bias == "sell" and price <= ema50)) else 0,
        "rsi":         float(analysis.get("rsi") or 50),
        "stoch":       float(analysis.get("stoch_k") or 50),
        "pd_pos":      float(pd_.get("position_pct") or 50),
        "adx":         float(analysis.get("adx") or 0),
        "is_buy":      1 if bias == "buy" else 0,
        "m4_neutral":  1 if macro4h == "neutral" else 0,
    }


# ── model ────────────────────────────────────────────────────────────────────
def load_model() -> bool:
    global _model, _meta
    if _model is not None:
        return True
    try:
        import joblib
        blob = joblib.load(MODEL_PATH)
        if blob.get("features") != FEATURE_ORDER:
            print("[META] feature order changed since training — model ignored")
            return False
        _model = blob["model"]
        _meta = {k: v for k, v in blob.items() if k != "model"}
        print(f"[META] model loaded — threshold {_meta.get('threshold'):.4f} "
              f"(keeps top {_meta.get('keep_pct')}%), trained {_meta.get('trained_at')}")
        return True
    except FileNotFoundError:
        print("[META] no meta_model.joblib — gate inactive (run train_meta_model.py)")
        return False
    except Exception as e:
        print(f"[META] load failed: {e}")
        return False


def get_threshold() -> float | None:
    return _meta.get("threshold") if _meta else None


def predict_r(analysis: dict, symbol: str, bias: str, macro4h: str,
              df_m5: pd.DataFrame | None = None) -> float | None:
    """
    Predicted R for this signal, or None when it cannot be scored (no model,
    not enough history). Callers must treat None as 'no opinion' and let the
    signal through — a scoring failure must never silently block trading.
    """
    if _model is None and not load_model():
        return None
    # Refuse to score a malformed analysis. signal_features() fills every field
    # with a default via `.get(...) or x`, so an empty dict yields a perfectly
    # valid-looking vector of defaults and a confident-looking prediction. With
    # the gate armed that would silently block a real signal on invented inputs.
    if not analysis or analysis.get("current_price") in (None, 0) \
            or analysis.get("scalping_score") is None:
        return None

    try:
        if df_m5 is None:
            from features.market.fetcher import fetch_candles
            df_m5 = fetch_candles(symbol, "5m", limit=FETCH_BARS)
        if df_m5 is None or len(df_m5) < MIN_BARS:
            return None
        s = regime_series(df_m5["high"].values, df_m5["low"].values, df_m5["close"].values)
        reg = regime_at(s, len(df_m5) - 1)
        if reg is None:
            return None
        row = {**signal_features(analysis, bias, macro4h), **reg}
        x = np.array([[row[f] for f in FEATURE_ORDER]], dtype=float)
        if np.isnan(x).any():
            return None
        return float(_model.predict(x)[0])
    except Exception as e:
        print(f"[META] scoring error {symbol}: {e}")
        return None
