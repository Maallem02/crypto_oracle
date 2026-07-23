"""
Zone model trainer — trains a gradient-boosted classifier on labeled zone touch events.

Library priority: LightGBM → XGBoost → sklearn GradientBoosting (fallback).
Install the best one available:  pip install lightgbm   or   pip install xgboost

Run once after build_dataset():
    from features.zones.trainer import run_full_pipeline
    run_full_pipeline()          # detect + label + train all symbols

Or trigger from API:  POST /zones/train
"""

import os
import pickle
import numpy as np
import pandas as pd
from datetime import datetime

ZONE_MODEL_PATH = "zone_model.pkl"
DATASET_PATH    = "zones_dataset.csv"
MIN_SAMPLES     = 200   # minimum labeled events before training is useful

# Must match FEATURE_COLS in labeler.py exactly (same order)
FEATURE_COLS = [
    "zone_type",
    "zone_age_candles",
    "touch_number",
    "prior_bounce_rate",
    "zone_width_atr",
    "rsi_at_touch",
    "approach_speed",
    "htf_trend",
    "hour",
    "day_of_week",
    "atr_ratio",
    "body_ratio",
]

_zone_model      = None
_zone_model_meta: dict = {"trained": False}


def _build_model():
    """Return (model, algo_name) using the best available library."""
    try:
        import lightgbm as lgb
        model = lgb.LGBMClassifier(
            n_estimators=500,
            max_depth=5,
            learning_rate=0.04,
            num_leaves=31,
            min_child_samples=10,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )
        return model, "LightGBM"
    except ImportError:
        pass

    try:
        from xgboost import XGBClassifier
        model = XGBClassifier(
            n_estimators=500,
            max_depth=5,
            learning_rate=0.04,
            eval_metric="logloss",
            use_label_encoder=False,
            random_state=42,
            verbosity=0,
        )
        return model, "XGBoost"
    except ImportError:
        pass

    from sklearn.ensemble import GradientBoostingClassifier
    model = GradientBoostingClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        random_state=42,
    )
    return model, "GradientBoosting(sklearn)"


def train_zone_model(dataset_path: str = DATASET_PATH) -> dict:
    """
    Load labeled dataset, train classifier, cross-validate, save to disk.
    Returns metadata dict (accuracy, AUC, feature importance …).
    """
    global _zone_model, _zone_model_meta

    if not os.path.exists(dataset_path):
        print(f"[ZONE-ML] Dataset not found: {dataset_path} — run build_dataset() first")
        return {}

    df = pd.read_csv(dataset_path)
    df = df.dropna(subset=FEATURE_COLS + ["label"])

    if len(df) < MIN_SAMPLES:
        print(f"[ZONE-ML] Not enough data: {len(df)}/{MIN_SAMPLES} events")
        return {}

    X = df[FEATURE_COLS].values.astype(float)
    y = df["label"].values.astype(int)

    bounce_rate = float(y.mean())
    print(f"[ZONE-ML] Dataset: {len(X)} events | bounce_rate={bounce_rate*100:.1f}%")

    from sklearn.model_selection import StratifiedKFold, cross_val_score

    model, algo = _build_model()

    # Explicit class_weight for XGBoost (no class_weight param)
    if algo == "XGBoost":
        scale = (y == 0).sum() / max((y == 1).sum(), 1)
        model.set_params(scale_pos_weight=scale)

    n_folds    = min(5, max(3, len(X) // 50))
    cv         = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    acc_scores = cross_val_score(model, X, y, cv=cv, scoring="accuracy")
    auc_scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")

    model.fit(X, y)

    # Feature importance
    if hasattr(model, "feature_importances_"):
        imp = dict(zip(
            FEATURE_COLS,
            [round(float(v), 4) for v in model.feature_importances_],
        ))
        imp = dict(sorted(imp.items(), key=lambda x: x[1], reverse=True))
    else:
        imp = {}

    _zone_model = model
    _zone_model_meta = {
        "trained":         True,
        "trained_at":      datetime.now().isoformat(),
        "algorithm":       algo,
        "samples":         int(len(X)),
        "bounce_rate_pct": round(bounce_rate * 100, 1),
        "accuracy_pct":    round(float(acc_scores.mean()) * 100, 1),
        "cv_std_pct":      round(float(acc_scores.std()) * 100, 1),
        "auc_pct":         round(float(auc_scores.mean()) * 100, 1),
        "feature_importance": imp,
    }

    with open(ZONE_MODEL_PATH, "wb") as f:
        pickle.dump({"model": _zone_model, "meta": _zone_model_meta}, f)

    print(f"[ZONE-ML] Trained ({algo}): {len(X)} events | "
          f"accuracy={_zone_model_meta['accuracy_pct']}% ± {_zone_model_meta['cv_std_pct']}% | "
          f"AUC={_zone_model_meta['auc_pct']}%")
    print(f"[ZONE-ML] Top features: {list(imp.items())[:4]}")
    return dict(_zone_model_meta)


def run_full_pipeline(
    symbols:  list = None,
    candles:  int  = 20000,
    timeframe: str = "1h",
) -> dict:
    """
    One-shot training pipeline:
      1. Detect + annotate zones per symbol (MT5 historical data)
      2. Extract labeled touch events → zones_dataset.csv
      3. Train zone model → zone_model.pkl

    Returns training metadata dict.
    Typical runtime: 60–120 s for 6 symbols × 20 000 H1 candles.
    """
    from features.zones.detector import build_zone_database
    from features.zones.labeler  import build_dataset

    print("[ZONE-PIPELINE] Step 1/3: Detecting and annotating zones …")
    db_summary = build_zone_database(symbols=symbols, timeframe=timeframe, candles=candles)
    print(f"[ZONE-PIPELINE] DB summary: {db_summary}")

    print("[ZONE-PIPELINE] Step 2/3: Extracting labeled touch events …")
    dataset = build_dataset(symbols=symbols, timeframe=timeframe, candles=candles)

    if dataset.empty:
        print("[ZONE-PIPELINE] No events extracted — aborting training.")
        return {}

    print("[ZONE-PIPELINE] Step 3/3: Training zone model …")
    return train_zone_model()


def load_zone_model() -> bool:
    """Load persisted model from disk into memory. Returns True on success."""
    global _zone_model, _zone_model_meta
    if not os.path.exists(ZONE_MODEL_PATH):
        return False
    try:
        with open(ZONE_MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
        _zone_model      = saved["model"]
        _zone_model_meta = saved["meta"]
        acc = _zone_model_meta.get("accuracy_pct", "?")
        n   = _zone_model_meta.get("samples", "?")
        print(f"[ZONE-ML] Model loaded: {n} events, accuracy={acc}%")
        return True
    except Exception as e:
        print(f"[ZONE-ML] Failed to load model: {e}")
        return False


def get_zone_model() -> object | None:
    """Return loaded model (load from disk on first call)."""
    global _zone_model
    if _zone_model is None:
        load_zone_model()
    return _zone_model


def get_zone_model_info() -> dict:
    if _zone_model is None:
        load_zone_model()
    return dict(_zone_model_meta)
