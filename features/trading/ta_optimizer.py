"""
ta_optimizer.py — Bidirectional AI <-> TA communication layer.

After every ML retrain:
  1. Translates feature importance  → dynamic TA scoring weights
  2. Tracks win rate per (symbol, tf) → priority multipliers
  3. Saves ta_config.json             → engine.py reads it live

Result: TA scoring weights evolve automatically as ML learns
what actually predicts profitable trades.  No human tuning needed.

Flow:
  train_model() --> update_from_ml(feature_importance)
       --> ta_config.json updated
       --> run_scalping_analysis() reads new weights on next call
"""
import json
import os
from datetime import datetime

TA_CONFIG_PATH = "ta_config.json"

# Points distributed ABOVE the mandatory LG base (50 pts).
# Total possible score = 50 + VARIABLE_POOL
VARIABLE_POOL = 60.0

# ML feature name  →  TA scoring category
# stoch_k and stoch_d are grouped together under "stoch"
FEATURE_TO_CATEGORY = {
    "stoch_k":      "stoch",
    "stoch_d":      "stoch",
    "rsi":          "rsi",
    "adx":          "adx",
    "lg_strength":  "lg_strength",
    "rr_ratio":     "rr_ratio",
    "atr_ratio":    "atr_ratio",
    "pd_pct":       "pd_pct",
    "htf_consensus":"htf",
    "structure":    "structure",
    "pd_zone":      "pd_zone",
    # Excluded: "hour", "day_of_week", "action", "scalping_score"
    # (time features and meta — not TA conditions)
}

# Default weights before first ML retrain (mirrors the original hand-tuned scoring)
DEFAULT_WEIGHTS = {
    "rsi":         25.0,
    "stoch":       25.0,
    "adx":          0.0,
    "lg_strength":  0.0,
    "rr_ratio":     0.0,
    "atr_ratio":    0.0,
    "pd_pct":       0.0,
    "htf":          0.0,
    "structure":   10.0,
    "pd_zone":      0.0,
}

DEFAULT_THRESHOLDS = {
    "rsi_max_buy":    70,
    "rsi_min_sell":   30,
    "stoch_max_buy":  80,
    "stoch_min_sell": 20,
    "adx_min":        15,
}

DEFAULT_CONFIG = {
    "scoring_weights":    DEFAULT_WEIGHTS,
    "thresholds":         DEFAULT_THRESHOLDS,
    "symbol_performance": {},
    "blend_factor":       20,   # ML prob contribution:  (prob-0.5) * blend_factor pts
    "updated_at":         None,
    "source":             "default",
}


# ── Config I/O ────────────────────────────────────────────────────────────────

def get_config() -> dict:
    """Return current ta_config.json, falling back to defaults."""
    if os.path.exists(TA_CONFIG_PATH):
        try:
            with open(TA_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            # Back-fill any missing keys with defaults
            cfg.setdefault("scoring_weights",    DEFAULT_WEIGHTS.copy())
            cfg.setdefault("thresholds",         DEFAULT_THRESHOLDS.copy())
            cfg.setdefault("symbol_performance", {})
            cfg.setdefault("blend_factor",       20)
            return cfg
        except Exception as e:
            print(f"[TAOptimizer] Config read error: {e} — using defaults")
    return DEFAULT_CONFIG.copy()


def _save_config(cfg: dict):
    with open(TA_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── Weight computation ────────────────────────────────────────────────────────

def _weights_from_importance(feature_importance: dict) -> dict:
    """
    Aggregate ML feature importances by TA category,
    then distribute VARIABLE_POOL points proportionally.
    """
    category_imp: dict[str, float] = {}
    for feat, imp in feature_importance.items():
        cat = FEATURE_TO_CATEGORY.get(feat)
        if cat:
            category_imp[cat] = category_imp.get(cat, 0.0) + float(imp)

    total = sum(category_imp.values())
    if total == 0:
        return DEFAULT_WEIGHTS.copy()

    weights = {cat: round(imp / total * VARIABLE_POOL, 1)
               for cat, imp in category_imp.items()}

    # Fill any missing categories
    for cat in DEFAULT_WEIGHTS:
        weights.setdefault(cat, 0.0)

    return weights


# ── Symbol / timeframe performance ───────────────────────────────────────────

def _symbol_performance() -> dict:
    """
    Win rate per (symbol, timeframe) from live DB trades (excludes imported history).
    Returns priority multiplier: 0.5 (bad) … 1.0 (neutral) … 1.5 (great).
    Requires at least 10 trades to count.
    """
    try:
        from core.database import get_db
        conn = get_db()
        rows = conn.execute("""
            SELECT symbol, timeframe,
                   COUNT(*)    AS trades,
                   SUM(outcome) AS wins
            FROM   trade_signals
            WHERE  outcome   IS NOT NULL
              AND  timeframe != 'imported'
            GROUP  BY symbol, timeframe
            HAVING trades >= 10
        """).fetchall()
        conn.close()

        perf = {}
        for symbol, tf, trades, wins in rows:
            win_rate = wins / trades
            # Linear: 50% wr = 1.0, 60% = 1.2, 40% = 0.8  (slope = 2)
            priority = round(1.0 + (win_rate - 0.5) * 2.0, 2)
            perf[f"{symbol}_{tf}"] = {
                "win_rate": round(win_rate, 3),
                "trades":   int(trades),
                "priority": max(0.5, min(1.5, priority)),
            }
        return perf
    except Exception as e:
        print(f"[TAOptimizer] symbol_performance error: {e}")
        return {}


# ── Main entry point (called by ml_model after retrain) ──────────────────────

def update_from_ml(feature_importance: dict) -> dict:
    """
    Called automatically after every ML retrain.
    Recomputes TA weights from new feature importance and updates ta_config.json.
    Returns the updated config dict.
    """
    old_cfg     = get_config()
    old_weights = old_cfg.get("scoring_weights", {})

    new_weights = _weights_from_importance(feature_importance)
    sym_perf    = _symbol_performance()

    new_cfg = {
        **old_cfg,
        "scoring_weights":    new_weights,
        "symbol_performance": sym_perf,
        "updated_at":         datetime.now().isoformat(),
        "source":             "ml_retrain",
    }
    _save_config(new_cfg)

    # ── Log the weight changes ────────────────────────────────────────────────
    print("[TAOptimizer] Scoring weights updated after ML retrain:")
    all_cats = sorted(set(list(old_weights.keys()) + list(new_weights.keys())))
    for cat in all_cats:
        old_v = old_weights.get(cat, 0.0)
        new_v = new_weights.get(cat, 0.0)
        delta = new_v - old_v
        arrow = "UP " if delta > 0.5 else ("DN " if delta < -0.5 else "   ")
        print(f"  {arrow} {cat:<15} {old_v:5.1f} -> {new_v:5.1f} pts")

    if sym_perf:
        print("[TAOptimizer] Symbol priorities:")
        for key, info in sorted(sym_perf.items(), key=lambda x: -x[1]["priority"]):
            print(f"  {key:<15} wr={info['win_rate']*100:.0f}%"
                  f"  trades={info['trades']}  priority={info['priority']}")

    return new_cfg


def get_symbol_priority(symbol: str, timeframe: str) -> float:
    """
    Returns the priority multiplier for a (symbol, timeframe) pair.
    Used by the router to boost/reduce score before ML filtering.
    1.0 = neutral, >1.0 = historically profitable, <1.0 = historically weak.
    """
    cfg  = get_config()
    perf = cfg.get("symbol_performance", {})
    key  = f"{symbol}_{timeframe}"
    return perf.get(key, {}).get("priority", 1.0)
