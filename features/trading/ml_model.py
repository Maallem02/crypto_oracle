"""
ML Decision Engine — PyTorch 3-class MLP.

The model IS the decision maker. Given current market conditions it outputs:
  buy      — conditions historically led to winning buy trades
  sell     — conditions historically led to winning sell trades
  no_trade — conditions historically led to losses regardless of direction

Training labels (derived from trade history):
  won_buy  → class 0  (buy)
  won_sell → class 1  (sell)
  any_loss → class 2  (no_trade)

Architecture:
  Input(20) → Linear(128) + BatchNorm + ReLU + Dropout(0.3)
            → Linear(64)  + BatchNorm + ReLU + Dropout(0.3)
            → Linear(32)  + BatchNorm + ReLU + Dropout(0.2)
            → Linear(3)   (raw logits → softmax at inference)

`action` is NOT an input feature — the model decides direction itself.
"""
import os
import numpy as np
from datetime import datetime

MODEL_PATH    = "ml_model.pt"
MIN_SAMPLES   = 60   # minimum labeled trades (across all 3 classes)
RETRAIN_EVERY = 10

# Decision labels
LABELS = ["buy", "sell", "no_trade"]  # indices 0, 1, 2

_model      = None
_imputer    = None   # np.ndarray — median per feature (replaces NaN)
_scaler     = None   # tuple (mean, std)
_model_meta = {
    "trained":            False,
    "trained_at":         None,
    "samples":            0,
    "accuracy_pct":       0.0,
    "last_sample_count":  0,
    "feature_importance": {},
    "class_distribution": {},
    "algorithm":          "PyTorch 3-class MLP (buy|sell|no_trade)",
}

# ── Feature definitions ────────────────────────────────────────────────────────
# `action` is removed — it becomes the label, not a feature.
# The model learns direction from market conditions alone.
NUMERIC_FEATURES = [
    "scalping_score", "adx", "atr_ratio", "rsi", "stoch_k", "stoch_d",
    "lg_strength", "pd_pct", "hour", "day_of_week", "rr_ratio",
    "volume_ratio", "spread_pct", "symbol_winrate", "consecutive_losses",
]  # 15

CATEGORICAL_FEATURES = {
    "structure":      {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0},
    "htf_consensus":  {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "conflict": -0.5},
    "htf_timeframe":  {"5m": 0.1, "15m": 0.2, "30m": 0.3, "1h": 0.5, "4h": 0.8, "1d": 1.0},
    "pd_zone":        {"premium": -1.0, "discount": 1.0, "equilibrium": 0.0,
                       "deep_premium": -1.5, "deep_discount": 1.5},
    "smc_confluence": {None: 0.0, "fvg": 0.5, "order_block": 1.0},
}  # 5

ALL_FEATURE_NAMES = NUMERIC_FEATURES + list(CATEGORICAL_FEATURES.keys())
N_FEATURES        = len(ALL_FEATURE_NAMES)   # 20


# ── PyTorch model ──────────────────────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn

    class TradeMLP(nn.Module):
        """3-class MLP: predicts buy / sell / no_trade from market conditions."""
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(N_FEATURES, 128),
                nn.BatchNorm1d(128),
                nn.ReLU(),
                nn.Dropout(0.3),

                nn.Linear(128, 64),
                nn.BatchNorm1d(64),
                nn.ReLU(),
                nn.Dropout(0.3),

                nn.Linear(64, 32),
                nn.BatchNorm1d(32),
                nn.ReLU(),
                nn.Dropout(0.2),

                nn.Linear(32, 3),  # raw logits — softmax applied at inference
            )

        def forward(self, x):
            return self.net(x)

    _TORCH_AVAILABLE = True

except ImportError:
    _TORCH_AVAILABLE = False
    TradeMLP          = None


# ── Feature helpers ────────────────────────────────────────────────────────────

def _row_to_vector(row: dict) -> list:
    vec = []
    for col in NUMERIC_FEATURES:
        v = row.get(col)
        vec.append(float(v) if v is not None else np.nan)
    for col, mapping in CATEGORICAL_FEATURES.items():
        v = row.get(col)
        vec.append(mapping.get(v, 0.0))
    return vec


def _preprocess(X_raw: np.ndarray, imputer: np.ndarray,
                mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    X = np.where(np.isnan(X_raw), imputer, X_raw)
    return (X - mean) / std


def _prepare_dataset(rows: list):
    """
    Map trade history to 3-class labels:
      won buy  → 0 (BUY)
      won sell → 1 (SELL)
      any loss → 2 (NO_TRADE)
    """
    X, y = [], []
    for row in rows:
        if row.get("outcome") is None:
            continue
        action  = (row.get("action") or "").lower()
        outcome = int(row["outcome"])

        if outcome == 1 and action == "buy":
            label = 0
        elif outcome == 1 and action == "sell":
            label = 1
        else:
            label = 2   # any loss = don't trade

        X.append(_row_to_vector(row))
        y.append(label)

    return np.array(X, dtype=float), np.array(y, dtype=int)


# ── Permutation importance ─────────────────────────────────────────────────────

def _permutation_importance(predict_fn, X: np.ndarray, y: np.ndarray) -> dict:
    base_preds = predict_fn(X).argmax(axis=1)
    base_acc   = (base_preds == y).mean()
    importances = {}
    for i, name in enumerate(ALL_FEATURE_NAMES):
        Xs = X.copy()
        Xs[:, i] = np.random.permutation(Xs[:, i])
        shuffled_acc = (predict_fn(Xs).argmax(axis=1) == y).mean()
        importances[name] = round(max(0.0, float(base_acc - shuffled_acc)), 4)
    total = sum(importances.values()) or 1.0
    return {k: round(v / total, 4) for k, v in importances.items()}


# ── Training ───────────────────────────────────────────────────────────────────

def train_model() -> dict | None:
    global _model, _imputer, _scaler, _model_meta

    if not _TORCH_AVAILABLE:
        print("[ML] PyTorch not installed — run: pip install torch")
        return None

    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader

    # Use the main account DB for richer training data
    try:
        from core.database import get_db
        conn = get_db()
        rows = conn.execute("""
            SELECT scalping_score, adx, atr_ratio, rsi, stoch_k, stoch_d,
                   structure, lg_strength, pd_zone, pd_pct,
                   htf_consensus, htf_timeframe,
                   volume_ratio, spread_pct, symbol_winrate, consecutive_losses,
                   smc_confluence, hour, day_of_week, rr_ratio,
                   action, outcome
            FROM trade_signals
            WHERE outcome IS NOT NULL
            ORDER BY timestamp ASC
        """).fetchall()
        conn.close()
        cols = ["scalping_score","adx","atr_ratio","rsi","stoch_k","stoch_d",
                "structure","lg_strength","pd_zone","pd_pct",
                "htf_consensus","htf_timeframe",
                "volume_ratio","spread_pct","symbol_winrate","consecutive_losses",
                "smc_confluence","hour","day_of_week","rr_ratio",
                "action","outcome"]
        data = [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        print(f"[ML] DB read failed: {e}")
        return None

    if len(data) < MIN_SAMPLES:
        print(f"[ML] Not enough data: {len(data)}/{MIN_SAMPLES} labeled trades")
        return None

    X_raw, y = _prepare_dataset(data)
    n = len(X_raw)

    # Class distribution
    n_buy      = int((y == 0).sum())
    n_sell     = int((y == 1).sum())
    n_no_trade = int((y == 2).sum())
    print(f"[ML] Dataset: {n} samples | buy={n_buy} sell={n_sell} no_trade={n_no_trade}")

    if n_buy < 5 or n_sell < 5:
        print("[ML] Too few winning trades per direction — need more data")
        return None

    # ── Impute NaN → column medians ───────────────────────────────────────────
    imputer_median = np.nanmedian(X_raw, axis=0)
    imputer_median = np.where(np.isnan(imputer_median), 0.0, imputer_median)
    X_imp = np.where(np.isnan(X_raw), imputer_median, X_raw)

    # ── Standardise ───────────────────────────────────────────────────────────
    scaler_mean = X_imp.mean(axis=0)
    scaler_std  = X_imp.std(axis=0)
    scaler_std  = np.where(scaler_std == 0, 1.0, scaler_std)
    X_scaled    = (X_imp - scaler_mean) / scaler_std

    # ── Chronological 80/20 split ─────────────────────────────────────────────
    split   = max(int(n * 0.8), MIN_SAMPLES)
    X_tr, X_val = X_scaled[:split], X_scaled[split:]
    y_tr, y_val = y[:split], y[split:]

    X_tr_t  = torch.tensor(X_tr,  dtype=torch.float32)
    y_tr_t  = torch.tensor(y_tr,  dtype=torch.long)
    X_val_t = torch.tensor(X_val, dtype=torch.float32)
    y_val_t = torch.tensor(y_val, dtype=torch.long)

    # ── Inverse-frequency class weights (handles 3-way imbalance) ────────────
    counts = np.array([n_buy, n_sell, n_no_trade], dtype=float)
    class_weights = torch.tensor(n / (3.0 * counts), dtype=torch.float32)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    # ── Model + optimiser ─────────────────────────────────────────────────────
    model     = TradeMLP()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", patience=10, factor=0.5, min_lr=1e-5
    )

    batch_size = min(32, max(8, len(X_tr_t) // 10))
    loader     = DataLoader(
        TensorDataset(X_tr_t, y_tr_t),
        batch_size=batch_size, shuffle=True, drop_last=False,
    )

    # ── Training loop with early stopping ─────────────────────────────────────
    best_val_loss = float("inf")
    best_state    = None
    no_improve    = 0
    patience      = 30
    max_epochs    = 400

    for epoch in range(max_epochs):
        model.train()
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

        model.eval()
        with torch.no_grad():
            val_loss = criterion(model(X_val_t), y_val_t).item() if len(X_val_t) > 0 else loss.item()

        scheduler.step(val_loss)

        if val_loss < best_val_loss - 1e-4:
            best_val_loss = val_loss
            best_state    = {k: v.clone() for k, v in model.state_dict().items()}
            no_improve    = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"[ML] Early stop at epoch {epoch + 1}")
                break

    if best_state:
        model.load_state_dict(best_state)
    model.eval()

    # ── Evaluation ────────────────────────────────────────────────────────────
    with torch.no_grad():
        logits = model(torch.tensor(X_scaled, dtype=torch.float32))
        probs  = torch.softmax(logits, dim=1).numpy()
        preds  = probs.argmax(axis=1)
    accuracy = float((preds == y).mean())

    # Per-class accuracy
    for i, label in enumerate(LABELS):
        mask = y == i
        if mask.sum() > 0:
            cls_acc = float((preds[mask] == i).mean())
            print(f"[ML] {label:>8} accuracy: {cls_acc*100:.1f}%  (n={mask.sum()})")

    try:
        from sklearn.metrics import roc_auc_score
        auc = float(roc_auc_score(
            np.eye(3)[y], probs, multi_class="ovr", average="macro"
        ))
    except Exception:
        auc = 0.0

    # ── Permutation importance ────────────────────────────────────────────────
    def _predict(X_np):
        with torch.no_grad():
            return torch.softmax(
                model(torch.tensor(X_np, dtype=torch.float32)), dim=1
            ).numpy()

    importances = _permutation_importance(_predict, X_scaled, y)
    top3 = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:3]
    print(f"[ML] Top features: {top3}")

    # ── Persist ───────────────────────────────────────────────────────────────
    _model   = model
    _imputer = imputer_median
    _scaler  = (scaler_mean, scaler_std)
    _model_meta.update({
        "trained":            True,
        "trained_at":         datetime.now().isoformat(),
        "samples":            int(n),
        "accuracy_pct":       round(accuracy * 100, 1),
        "auc_pct":            round(auc * 100, 1),
        "last_sample_count":  len(data),
        "feature_importance": importances,
        "class_distribution": {"buy": n_buy, "sell": n_sell, "no_trade": n_no_trade},
        "algorithm":          "PyTorch 3-class MLP (buy|sell|no_trade)",
    })

    torch.save({
        "model_state":    model.state_dict(),
        "meta":           _model_meta,
        "imputer_median": imputer_median,
        "scaler_mean":    scaler_mean,
        "scaler_std":     scaler_std,
        "n_features":     N_FEATURES,
    }, MODEL_PATH)

    print(f"[ML] Trained: {n} samples | accuracy={accuracy*100:.1f}% | AUC={auc*100:.1f}%")
    print(f"[ML] Saved to {MODEL_PATH}")

    try:
        from features.trading.ta_optimizer import update_from_ml
        update_from_ml(importances)
    except Exception as e:
        print(f"[TAOptimizer] update skipped: {e}")

    return dict(_model_meta)


# ── Persistence ────────────────────────────────────────────────────────────────

def load_model() -> bool:
    global _model, _imputer, _scaler, _model_meta
    if not _TORCH_AVAILABLE or not os.path.exists(MODEL_PATH):
        return False
    import torch
    try:
        saved = torch.load(MODEL_PATH, weights_only=False)
        # Guard: file might be old binary (sklearn pickle), not our 3-class format
        if not isinstance(saved, dict) or "model_state" not in saved:
            print("[ML] Old model format detected — retraining required")
            return False
        model = TradeMLP()
        model.load_state_dict(saved["model_state"])
        model.eval()
        _model      = model
        _imputer    = saved["imputer_median"]
        _scaler     = (saved["scaler_mean"], saved["scaler_std"])
        _model_meta = saved["meta"]
        acc  = _model_meta.get("accuracy_pct", "?")
        n    = _model_meta.get("samples", "?")
        dist = _model_meta.get("class_distribution", {})
        print(f"[ML] 3-class MLP loaded: {n} samples, accuracy={acc}%  dist={dist}")
        return True
    except Exception as e:
        print(f"[ML] Failed to load model: {e}")
        return False


def maybe_retrain():
    from features.trading.data_collector import get_stats
    stats   = get_stats()
    labeled = stats["labeled"]
    last    = _model_meta.get("last_sample_count", 0)
    if labeled >= MIN_SAMPLES and (labeled - last) >= RETRAIN_EVERY:
        print(f"[ML] Auto-retrain: {labeled} total (+{labeled - last} new)")
        train_model()


# ── Inference ──────────────────────────────────────────────────────────────────

def _build_row(analysis, entry_data, htf_consensus, htf_timeframe, extra) -> dict:
    lg    = analysis.get("liquidity_grab", {}) or {}
    pd    = analysis.get("premium_discount", {}) or {}
    extra = extra or {}
    return {
        "scalping_score":     analysis.get("scalping_score"),
        "adx":                analysis.get("adx"),
        "atr_ratio":          analysis.get("atr_ratio"),
        "rsi":                analysis.get("rsi"),
        "stoch_k":            analysis.get("stoch_k"),
        "stoch_d":            analysis.get("stoch_d"),
        "lg_strength":        lg.get("strength", 0),
        "pd_pct":             pd.get("position_pct"),
        "hour":               datetime.now().hour,
        "day_of_week":        datetime.now().weekday(),
        "rr_ratio":           entry_data.get("rr_ratio"),
        "volume_ratio":       extra.get("volume_ratio"),
        "spread_pct":         extra.get("spread_pct"),
        "symbol_winrate":     extra.get("symbol_winrate"),
        "consecutive_losses": extra.get("consecutive_losses"),
        "structure":          (analysis.get("structure") or {}).get("trend"),
        "htf_consensus":      htf_consensus,
        "htf_timeframe":      htf_timeframe,
        "pd_zone":            pd.get("zone"),
        "smc_confluence":     analysis.get("smc_confluence"),
    }


def predict_decision(
    analysis:      dict,
    entry_data:    dict,
    htf_consensus: str,
    htf_timeframe: str  = None,
    extra:         dict = None,
    symbol:        str  = None,
) -> dict:
    """
    Ensemble inference: MLP (TA features) + CNN (raw candle patterns).

    MLP is the primary decision maker.
    CNN acts as a secondary gate:
      - MLP buy/sell + CNN same direction → CONFIRM (boost confidence)
      - MLP buy/sell + CNN no_trade      → PASS (MLP decision stands)
      - MLP buy/sell + CNN opposite      → CONFLICT → no_trade
      - MLP no_trade                     → no_trade (CNN cannot override)
      - CNN not ready                    → MLP only

    Returns:
        {
          'action':     'buy' | 'sell' | 'no_trade',
          'confidence': float,
          'probs':      {'buy': f, 'sell': f, 'no_trade': f},
          'ready':      bool,
          'cnn':        dict | None   # raw CNN result for logging
        }
    """
    if not _TORCH_AVAILABLE or _model is None:
        load_model()
    if _model is None:
        return {"action": "no_trade", "confidence": 0.0,
                "probs": {"buy": 0.33, "sell": 0.33, "no_trade": 0.34},
                "ready": False, "cnn": None}

    import torch

    # ── MLP decision ─────────────────────────────────────────────────────────
    row = _build_row(analysis, entry_data, htf_consensus, htf_timeframe, extra)
    vec = np.array(_row_to_vector(row), dtype=float)
    vec = _preprocess(vec.reshape(1, -1), _imputer, *_scaler)[0]

    _model.eval()
    with torch.no_grad():
        logits = _model(torch.tensor(vec, dtype=torch.float32).unsqueeze(0))
        probs  = torch.softmax(logits, dim=1)[0].numpy()

    buy_p, sell_p, no_trade_p = float(probs[0]), float(probs[1]), float(probs[2])

    best_idx = int(probs.argmax())
    mlp_action = LABELS[best_idx]
    mlp_conf   = float(probs[best_idx])

    if mlp_action in ("buy", "sell") and mlp_conf < 0.30:
        mlp_action = "no_trade"
        mlp_conf   = no_trade_p

    # ── CNN ensemble (only when MLP wants to trade and symbol is known) ──────
    cnn_result = None
    final_action = mlp_action
    final_conf   = mlp_conf

    if mlp_action in ("buy", "sell") and symbol:
        try:
            from features.trading.cnn_model import predict_cnn, load_cnn
            cnn_result = predict_cnn(symbol)
            if cnn_result["ready"]:
                cnn_action = cnn_result["action"]
                cnn_conf   = cnn_result["confidence"]
                if cnn_action == mlp_action:
                    # Both agree — blend confidence upward slightly
                    blended_conf = round(mlp_conf * 0.7 + cnn_conf * 0.3, 3)
                    final_conf   = blended_conf
                    print(f"[CNN] ✓ Confirms {mlp_action.upper()} "
                          f"(mlp={mlp_conf:.2f} cnn={cnn_conf:.2f} → {blended_conf:.2f})")
                elif cnn_action == "no_trade":
                    # CNN uncertain — MLP decision stands unchanged
                    print(f"[CNN] ~ Neutral on {mlp_action.upper()} "
                          f"(cnn_no_trade={cnn_result['probs']['no_trade']:.2f})")
                else:
                    # CNN says opposite direction → block the trade
                    print(f"[CNN] ✗ CONFLICT: MLP={mlp_action.upper()} "
                          f"vs CNN={cnn_action.upper()} → NO TRADE")
                    final_action = "no_trade"
                    final_conf   = no_trade_p
        except Exception as e:
            print(f"[CNN] Skipped: {e}")

    return {
        "action":     final_action,
        "confidence": round(final_conf, 3),
        "probs":      {
            "buy":      round(buy_p, 3),
            "sell":     round(sell_p, 3),
            "no_trade": round(no_trade_p, 3),
        },
        "ready": True,
        "cnn":   cnn_result,
    }


def predict_win_probability(
    analysis:      dict,
    entry_data:    dict,
    htf_consensus: str,
    htf_timeframe: str  = None,
    extra:         dict = None,
) -> float:
    """
    Backward-compat shim for the ml_boost score adjustment in the router.
    Returns the probability of the direction that matches analysis bias.
    """
    dec  = predict_decision(analysis, entry_data, htf_consensus, htf_timeframe, extra)
    bias = (analysis.get("bias") or "").lower()
    if bias == "buy":
        return dec["probs"]["buy"]
    if bias == "sell":
        return dec["probs"]["sell"]
    return 1.0 - dec["probs"]["no_trade"]


def get_lot_multiplier(ml_prob: float) -> float:
    return 2.0 if ml_prob >= 0.80 else 1.0


def get_model_info() -> dict:
    if _model is None:
        load_model()
    from features.trading.data_collector import get_stats
    stats = get_stats()

    cnn_info = {}
    try:
        from features.trading.cnn_model import get_cnn_info
        cnn_info = get_cnn_info()
    except Exception:
        pass

    return {
        **_model_meta,
        "data_stats":      stats,
        "model_path":      MODEL_PATH,
        "filter_active":   _model is not None,
        "torch_available": _TORCH_AVAILABLE,
        "cnn":             cnn_info,
    }
