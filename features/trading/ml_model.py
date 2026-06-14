"""
ML signal filter — Random Forest trained on historical trade outcomes.

Flow:
  scalp_auto_scan → predict_win_probability() → place trade only if prob >= threshold
  every 30 new labeled samples → auto-retrain
  model persisted to ml_model.pkl (survives restarts)

Cold start: returns 0.5 (neutral) when model not yet trained → trades not filtered.
"""
import os
import pickle
import numpy as np
from datetime import datetime

MODEL_PATH    = "ml_model.pkl"
MIN_SAMPLES   = 50   # minimum labeled trades to activate the filter
RETRAIN_EVERY = 10   # new labeled samples between auto-retrains (lower = learns faster)

_model     = None
_model_meta = {
    "trained":           False,
    "trained_at":        None,
    "samples":           0,
    "accuracy_pct":      0.0,
    "last_sample_count": 0,
    "feature_importance": {},
}

# Numeric features (directly from analysis dict or computed)
NUMERIC_FEATURES = [
    "scalping_score",
    "adx",
    "atr_ratio",
    "rsi",
    "stoch_k",
    "stoch_d",
    "lg_strength",
    "pd_pct",
    "hour",
    "day_of_week",
    "rr_ratio",
]

# Categorical → numeric encoding
CATEGORICAL_FEATURES = {
    "structure":    {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0},
    "htf_consensus":{"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "conflict": -0.5},
    "pd_zone":      {"premium": -1.0, "discount": 1.0, "equilibrium": 0.0},
    "action":       {"buy": 1.0, "sell": -1.0},
}

ALL_FEATURE_NAMES = NUMERIC_FEATURES + list(CATEGORICAL_FEATURES.keys())


def _row_to_vector(row: dict) -> list:
    vec = []
    for col in NUMERIC_FEATURES:
        v = row.get(col)
        vec.append(float(v) if v is not None else np.nan)
    for col, mapping in CATEGORICAL_FEATURES.items():
        v = row.get(col)
        vec.append(mapping.get(v, 0.0))
    return vec


def _prepare_dataset(rows: list):
    X, y = [], []
    for row in rows:
        if row.get("outcome") is None:
            continue
        X.append(_row_to_vector(row))
        y.append(int(row["outcome"]))
    return np.array(X, dtype=float), np.array(y, dtype=int)


def train_model() -> dict | None:
    """Train (or retrain) on all labeled data. Returns meta dict or None."""
    global _model, _model_meta

    try:
        from sklearn.ensemble import (
            RandomForestClassifier,
            HistGradientBoostingClassifier,
            VotingClassifier,
        )
        from sklearn.model_selection import StratifiedKFold, cross_val_score
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
    except ImportError:
        print("[ML] scikit-learn not installed — run: pip install scikit-learn")
        return None

    from features.trading.data_collector import get_training_data
    data = get_training_data()

    if len(data) < MIN_SAMPLES:
        print(f"[ML] Not enough data: {len(data)}/{MIN_SAMPLES} labeled trades")
        return None

    X, y = _prepare_dataset(data)
    if len(X) < MIN_SAMPLES:
        return None

    win_rate = float(y.mean())
    print(f"[ML] Dataset: {len(X)} samples | win_rate={win_rate*100:.1f}%")

    # ── Ensemble: RandomForest + HistGradientBoosting ─────────────────────────
    # HistGradientBoosting handles NaN natively — no imputer needed for it.
    # RF still needs imputation, so we keep the SimpleImputer in a sub-pipeline.
    rf = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=300,
            max_depth=6,
            min_samples_leaf=4,
            class_weight="balanced",
            random_state=42,
        )),
    ])

    hgb = HistGradientBoostingClassifier(
        max_iter=300,
        max_depth=5,
        min_samples_leaf=4,
        class_weight="balanced",
        random_state=42,
    )

    ensemble = VotingClassifier(
        estimators=[("rf", rf), ("hgb", hgb)],
        voting="soft",          # average predicted probabilities
        weights=[1, 2],         # HGB gets 2× weight (usually better)
    )

    # ── Cross-validation ──────────────────────────────────────────────────────
    n_folds = min(5, max(3, len(X) // 20))
    cv      = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    acc_scores = cross_val_score(ensemble, X, y, cv=cv, scoring="accuracy")
    auc_scores = cross_val_score(ensemble, X, y, cv=cv, scoring="roc_auc")

    # ── Final fit ─────────────────────────────────────────────────────────────
    # VotingClassifier with voting='soft' averages probabilities across both
    # estimators — already well-calibrated without needing extra calibration.
    ensemble.fit(X, y)

    # ── Feature importance: RF importances (HGB doesn't expose this) ─────────
    rf_clf  = ensemble.named_estimators_["rf"].named_steps["clf"]
    importances = dict(zip(
        ALL_FEATURE_NAMES,
        [round(float(v), 4) for v in rf_clf.feature_importances_],
    ))
    importances = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True))

    _model = ensemble
    _model_meta.update({
        "trained":            True,
        "trained_at":         datetime.now().isoformat(),
        "samples":            int(len(X)),
        "win_rate_pct":       round(win_rate * 100, 1),
        "accuracy_pct":       round(float(acc_scores.mean()) * 100, 1),
        "cv_std_pct":         round(float(acc_scores.std()) * 100, 1),
        "auc_pct":            round(float(auc_scores.mean()) * 100, 1),
        "auc_std_pct":        round(float(auc_scores.std()) * 100, 1),
        "last_sample_count":  len(data),
        "feature_importance": importances,
        "algorithm":          "VotingEnsemble(RF×1 + HGB×2, soft voting)",
    })

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": _model, "meta": _model_meta}, f)

    acc = _model_meta["accuracy_pct"]
    auc = _model_meta["auc_pct"]
    print(f"[ML] Trained: {len(X)} samples | accuracy={acc}% ± {_model_meta['cv_std_pct']}% | AUC={auc}%")

    # ── Feed results back into TA engine (bidirectional loop) ─────────────────
    try:
        from features.trading.ta_optimizer import update_from_ml
        update_from_ml(importances)
    except Exception as e:
        print(f"[TAOptimizer] update skipped: {e}")
    top3 = list(importances.items())[:3]
    print(f"[ML] Top features: {top3}")
    return dict(_model_meta)


def load_model() -> bool:
    """Load persisted model from disk."""
    global _model, _model_meta
    if not os.path.exists(MODEL_PATH):
        return False
    try:
        with open(MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
        _model      = saved["model"]
        _model_meta = saved["meta"]
        acc = _model_meta.get("accuracy_pct", "?")
        n   = _model_meta.get("samples", "?")
        print(f"[ML] Model loaded from disk: {n} samples, accuracy={acc}%")
        return True
    except Exception as e:
        print(f"[ML] Failed to load model: {e}")
        return False


def maybe_retrain():
    """Call at each scan cycle. Retrains if RETRAIN_EVERY new labeled samples arrived."""
    from features.trading.data_collector import get_stats
    stats  = get_stats()
    labeled = stats["labeled"]
    last   = _model_meta.get("last_sample_count", 0)
    new    = labeled - last

    if labeled >= MIN_SAMPLES and new >= RETRAIN_EVERY:
        print(f"[ML] Auto-retrain: {labeled} total (+{new} new)")
        train_model()


def predict_win_probability(
    analysis:      dict,
    entry_data:    dict,
    htf_consensus: str,
) -> float:
    """
    Returns win probability [0.0–1.0].
    Returns 0.5 (neutral / no filter) when model is not yet available.
    """
    global _model

    if _model is None:
        load_model()
    if _model is None:
        return 0.5

    lg = analysis.get("liquidity_grab", {}) or {}
    pd = analysis.get("premium_discount", {}) or {}

    row = {
        "scalping_score": analysis.get("scalping_score"),
        "adx":            analysis.get("adx"),
        "atr_ratio":      analysis.get("atr_ratio"),
        "rsi":            analysis.get("rsi"),
        "stoch_k":        analysis.get("stoch_k"),
        "stoch_d":        analysis.get("stoch_d"),
        "lg_strength":    lg.get("strength", 0),
        "pd_pct":         pd.get("position_pct"),
        "hour":           datetime.now().hour,
        "day_of_week":    datetime.now().weekday(),
        "rr_ratio":       entry_data.get("rr_ratio"),
        "structure":      (analysis.get("structure") or {}).get("trend"),
        "htf_consensus":  htf_consensus,
        "pd_zone":        pd.get("zone"),
        "action":         analysis.get("bias"),
    }

    X = np.array([_row_to_vector(row)], dtype=float)
    try:
        prob = float(_model.predict_proba(X)[0][1])
        return round(prob, 3)
    except Exception as e:
        print(f"[ML] predict error: {e}")
        return 0.5


def get_model_info() -> dict:
    if _model is None:
        load_model()
    from features.trading.data_collector import get_stats
    stats = get_stats()
    return {
        **_model_meta,
        "data_stats":    stats,
        "model_path":    MODEL_PATH,
        "filter_active": _model is not None and stats["labeled"] >= MIN_SAMPLES,
    }
