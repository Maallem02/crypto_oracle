"""
cnn_model.py — 1D CNN on candlestick sequences.

Input  : last 50 × 1h OHLCV candles, normalised per-window to [0, 1]
Output : buy | sell | no_trade  (same 3 classes as the MLP)

The CNN is completely independent of pre-computed indicators — it reads
raw price action directly. Combined with the MLP (which reads TA features)
they give two independent opinions. A trade is taken only when both agree
on the same direction.

Architecture:
  (50, 5) candles
    → Conv1d(5→32,  k=3) + BN + ReLU
    → Conv1d(32→64, k=3) + BN + ReLU   → MaxPool(2)   50→25
    → Conv1d(64→128,k=3) + BN + ReLU
    → Conv1d(128→128,k=3)+ BN + ReLU   → MaxPool(2)   25→12
    → AdaptiveAvgPool(1)               → (128,)
    → Linear(128→64) + ReLU + Dropout(0.4)
    → Linear(64→3)   raw logits
"""
import os
import numpy as np
from datetime import datetime, timedelta

CNN_MODEL_PATH = "cnn_model.pt"
N_CANDLES      = 50        # lookback window (1h × 50 = ~2 trading days)
N_FEATURES     = 5         # O H L C V
CNN_TIMEFRAME  = "1h"      # candle resolution fed to the CNN

LABELS = ["buy", "sell", "no_trade"]

_cnn_model  = None
_cnn_meta   = {
    "trained":           False,
    "trained_at":        None,
    "samples":           0,
    "accuracy_pct":      0.0,
    "auc_pct":           0.0,
    "class_distribution": {},
    "algorithm":         f"PyTorch 1D-CNN ({N_CANDLES}×{CNN_TIMEFRAME} OHLCV)",
}

# ── PyTorch model ──────────────────────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn

    class CandleCNN(nn.Module):
        """1D CNN that reads OHLCV sequences and classifies market direction."""
        def __init__(self):
            super().__init__()
            self.block1 = nn.Sequential(
                nn.Conv1d(N_FEATURES, 32, kernel_size=3, padding=1),
                nn.BatchNorm1d(32),
                nn.ReLU(),
                nn.Conv1d(32, 64, kernel_size=3, padding=1),
                nn.BatchNorm1d(64),
                nn.ReLU(),
                nn.MaxPool1d(2),
                nn.Dropout(0.2),
            )
            self.block2 = nn.Sequential(
                nn.Conv1d(64, 128, kernel_size=3, padding=1),
                nn.BatchNorm1d(128),
                nn.ReLU(),
                nn.Conv1d(128, 128, kernel_size=3, padding=1),
                nn.BatchNorm1d(128),
                nn.ReLU(),
                nn.MaxPool1d(2),
                nn.Dropout(0.2),
            )
            self.global_pool = nn.AdaptiveAvgPool1d(1)
            self.head = nn.Sequential(
                nn.Flatten(),
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.Dropout(0.4),
                nn.Linear(64, 3),
            )

        def forward(self, x):
            # x: (batch, N_CANDLES, N_FEATURES) → (batch, N_FEATURES, N_CANDLES)
            x = x.transpose(1, 2)
            x = self.block1(x)
            x = self.block2(x)
            x = self.global_pool(x)
            return self.head(x)

    _TORCH_AVAILABLE = True

except ImportError:
    _TORCH_AVAILABLE = False
    CandleCNN         = None


# ── Candle normalisation ───────────────────────────────────────────────────────

def _normalise_window(candles: np.ndarray) -> np.ndarray:
    """
    Normalise a (N_CANDLES, 5) OHLCV window to [0, 1] per window.
    Price channels (OHLC): divided by the window price range so that
    patterns look the same whether ETH=$1700 or BTC=$61000.
    Volume channel: divided by the window's max volume.
    Returns NaN-free float32 array.
    """
    result = candles.astype(np.float32).copy()
    # OHLC
    price_min = result[:, :4].min()
    price_max = result[:, :4].max()
    rng = price_max - price_min
    if rng > 0:
        result[:, :4] = (result[:, :4] - price_min) / rng
    # Volume
    vol_max = result[:, 4].max()
    if vol_max > 0:
        result[:, 4] = result[:, 4] / vol_max
    result = np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=0.0)
    return result


# ── Symbol resolution (e.g. "ETH" → "ETHUSDm") ────────────────────────────────

_sym_cache: dict[str, str] = {}

def _resolve_mt5_symbol(short: str) -> str | None:
    """Find the broker's full symbol name for a short name like 'ETH'."""
    if short in _sym_cache:
        return _sym_cache[short]
    try:
        import MetaTrader5 as mt5
        # Direct match first
        if mt5.symbol_info(short):
            _sym_cache[short] = short
            return short
        # Search by prefix
        candidates = mt5.symbols_get(f"{short}*") or []
        for s in candidates:
            if s.name.upper().startswith(short.upper()):
                _sym_cache[short] = s.name
                return s.name
    except Exception:
        pass
    return None


# ── MT5 candle fetching for a specific past timestamp ─────────────────────────

def _fetch_candles_at(symbol: str, tf: str, at_time: datetime,
                      count: int = N_CANDLES) -> np.ndarray | None:
    """
    Fetch `count` 1h candles ending at (or just before) `at_time`.
    Returns (count, 5) OHLCV numpy array, or None if unavailable.
    """
    try:
        import MetaTrader5 as mt5
        TF_MAP = {
            "1m": mt5.TIMEFRAME_M1,  "5m":  mt5.TIMEFRAME_M5,
            "15m": mt5.TIMEFRAME_M15,"30m": mt5.TIMEFRAME_M30,
            "1h": mt5.TIMEFRAME_H1,  "4h":  mt5.TIMEFRAME_H4,
            "1d": mt5.TIMEFRAME_D1,
        }
        tf_code    = TF_MAP.get(tf)
        if tf_code is None:
            return None
        mt5_symbol = _resolve_mt5_symbol(symbol)
        if mt5_symbol is None:
            return None

        # Fetch from (at_time - count×bar_width) to at_time
        bars = mt5.copy_rates_range(
            mt5_symbol, tf_code,
            at_time - timedelta(hours=count + 5),   # small buffer
            at_time,
        )
        if bars is None or len(bars) < 5:
            return None

        arr = np.array([[b['open'], b['high'], b['low'], b['close'],
                         b['tick_volume']] for b in bars[-count:]], dtype=np.float32)
        if len(arr) < count:
            # Pad with first row if slightly short
            pad = np.tile(arr[0], (count - len(arr), 1))
            arr = np.vstack([pad, arr])
        return arr[:count]
    except Exception:
        return None


# ── Dataset builder ────────────────────────────────────────────────────────────

def build_cnn_dataset(verbose: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """
    For every trade in trade_signals with a known outcome, fetch the
    50 × 1h candles that existed BEFORE the trade opened from MT5 history.
    Returns (X, y) where X is (n, N_CANDLES, N_FEATURES) and y is (n,).
    """
    try:
        from core.database import get_db
        conn = get_db()
        rows = conn.execute("""
            SELECT timestamp, symbol, action, outcome
            FROM trade_signals
            WHERE outcome IS NOT NULL
            ORDER BY timestamp ASC
        """).fetchall()
        conn.close()
    except Exception as e:
        print(f"[CNN] DB read failed: {e}")
        return np.array([]), np.array([])

    X, y = [], []
    skipped = 0

    for ts_str, symbol, action, outcome in rows:
        try:
            # Parse timestamp
            ts = datetime.fromisoformat(ts_str.replace("Z", ""))
        except Exception:
            skipped += 1
            continue

        # Fetch candles that existed at that moment
        candles = _fetch_candles_at(symbol, CNN_TIMEFRAME, ts)
        if candles is None:
            skipped += 1
            continue

        norm = _normalise_window(candles)

        # Label: won_buy=0, won_sell=1, loss=2
        action_l = (action or "").lower()
        if outcome == 1 and action_l == "buy":
            label = 0
        elif outcome == 1 and action_l == "sell":
            label = 1
        else:
            label = 2

        X.append(norm)
        y.append(label)

    if verbose:
        n_buy  = sum(1 for l in y if l == 0)
        n_sell = sum(1 for l in y if l == 1)
        n_no   = sum(1 for l in y if l == 2)
        print(f"[CNN] Dataset: {len(X)} candle sequences "
              f"(buy={n_buy} sell={n_sell} no_trade={n_no}) | skipped={skipped}")

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


# ── Training ───────────────────────────────────────────────────────────────────

def train_cnn() -> dict | None:
    global _cnn_model, _cnn_meta

    if not _TORCH_AVAILABLE:
        print("[CNN] PyTorch not installed — run: pip install torch")
        return None

    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader

    print("[CNN] Building dataset from MT5 history ...")
    X, y = build_cnn_dataset()

    if len(X) < 40:
        print(f"[CNN] Not enough usable samples: {len(X)} (need 40+)")
        return None

    n        = len(X)
    n_buy    = int((y == 0).sum())
    n_sell   = int((y == 1).sum())
    n_no     = int((y == 2).sum())

    # Chronological 80/20 split
    split   = max(int(n * 0.8), 30)
    X_tr    = torch.tensor(X[:split],  dtype=torch.float32)
    y_tr    = torch.tensor(y[:split],  dtype=torch.long)
    X_val   = torch.tensor(X[split:],  dtype=torch.float32)
    y_val   = torch.tensor(y[split:],  dtype=torch.long)

    # Inverse-frequency class weights
    counts       = np.array([n_buy, n_sell, n_no], dtype=float)
    class_weights = torch.tensor(n / (3.0 * np.maximum(counts, 1)), dtype=torch.float32)
    criterion    = nn.CrossEntropyLoss(weight=class_weights)

    model     = CandleCNN()
    optimizer = torch.optim.Adam(model.parameters(), lr=5e-4, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, patience=10, factor=0.5, min_lr=1e-5
    )

    batch_size = min(32, max(8, len(X_tr) // 10))
    loader     = DataLoader(TensorDataset(X_tr, y_tr),
                            batch_size=batch_size, shuffle=True, drop_last=False)

    best_val_loss = float("inf")
    best_state    = None
    no_improve    = 0
    patience      = 30

    print(f"[CNN] Training on {len(X_tr)} samples, validating on {len(X_val)} ...")

    for epoch in range(400):
        model.train()
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

        model.eval()
        with torch.no_grad():
            val_loss = criterion(model(X_val), y_val).item() if len(X_val) > 0 else loss.item()

        scheduler.step(val_loss)

        if val_loss < best_val_loss - 1e-4:
            best_val_loss = val_loss
            best_state    = {k: v.clone() for k, v in model.state_dict().items()}
            no_improve    = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"[CNN] Early stop at epoch {epoch + 1}")
                break

    if best_state:
        model.load_state_dict(best_state)
    model.eval()

    # Evaluate
    with torch.no_grad():
        logits = model(torch.tensor(X, dtype=torch.float32))
        probs  = torch.softmax(logits, dim=1).numpy()
        preds  = probs.argmax(axis=1)
    accuracy = float((preds == y).mean())

    for i, lbl in enumerate(LABELS):
        mask = y == i
        if mask.sum() > 0:
            acc = float((preds[mask] == i).mean())
            print(f"[CNN] {lbl:>8} accuracy: {acc*100:.1f}%  (n={mask.sum()})")

    try:
        from sklearn.metrics import roc_auc_score
        auc = float(roc_auc_score(np.eye(3)[y], probs,
                                  multi_class="ovr", average="macro"))
    except Exception:
        auc = 0.0

    _cnn_model = model
    _cnn_meta.update({
        "trained":            True,
        "trained_at":         datetime.now().isoformat(),
        "samples":            int(n),
        "accuracy_pct":       round(accuracy * 100, 1),
        "auc_pct":            round(auc * 100, 1),
        "class_distribution": {"buy": n_buy, "sell": n_sell, "no_trade": n_no},
        "algorithm":          f"PyTorch 1D-CNN ({N_CANDLES}×{CNN_TIMEFRAME} OHLCV)",
    })

    torch.save({"model_state": model.state_dict(), "meta": _cnn_meta}, CNN_MODEL_PATH)
    print(f"[CNN] Trained: {n} samples | accuracy={accuracy*100:.1f}% | AUC={auc*100:.1f}%")
    print(f"[CNN] Saved → {CNN_MODEL_PATH}")
    return dict(_cnn_meta)


# ── Persistence ────────────────────────────────────────────────────────────────

def load_cnn() -> bool:
    global _cnn_model, _cnn_meta
    if not _TORCH_AVAILABLE or not os.path.exists(CNN_MODEL_PATH):
        return False
    import torch
    try:
        saved      = torch.load(CNN_MODEL_PATH, weights_only=False)
        model      = CandleCNN()
        model.load_state_dict(saved["model_state"])
        model.eval()
        _cnn_model = model
        _cnn_meta  = saved["meta"]
        acc = _cnn_meta.get("accuracy_pct", "?")
        n   = _cnn_meta.get("samples", "?")
        print(f"[CNN] Loaded: {n} samples, accuracy={acc}%")
        return True
    except Exception as e:
        print(f"[CNN] Load failed: {e}")
        return False


# ── Inference ──────────────────────────────────────────────────────────────────

def predict_cnn(symbol: str) -> dict:
    """
    Fetch the last N_CANDLES 1h candles for `symbol` and run through the CNN.
    Returns {'action': 'buy'|'sell'|'no_trade', 'confidence': float,
             'probs': {...}, 'ready': bool}
    """
    _default = {"action": "no_trade", "confidence": 0.0,
                "probs": {"buy": 0.33, "sell": 0.33, "no_trade": 0.34}, "ready": False}

    if not _TORCH_AVAILABLE:
        return _default
    if _cnn_model is None:
        load_cnn()
    if _cnn_model is None:
        return _default

    import torch

    candles = _fetch_candles_at(symbol, CNN_TIMEFRAME, datetime.now())
    if candles is None:
        print(f"[CNN] {symbol}: could not fetch live candles")
        return _default

    norm = _normalise_window(candles)
    x    = torch.tensor(norm, dtype=torch.float32).unsqueeze(0)  # (1, 50, 5)

    _cnn_model.eval()
    with torch.no_grad():
        logits = _cnn_model(x)
        probs  = torch.softmax(logits, dim=1)[0].numpy()

    buy_p, sell_p, no_p = float(probs[0]), float(probs[1]), float(probs[2])
    best_idx = int(probs.argmax())
    action   = LABELS[best_idx]
    conf     = float(probs[best_idx])

    if action in ("buy", "sell") and conf < 0.30:
        action = "no_trade"
        conf   = no_p

    return {
        "action":     action,
        "confidence": round(conf, 3),
        "probs":      {"buy": round(buy_p, 3), "sell": round(sell_p, 3),
                       "no_trade": round(no_p, 3)},
        "ready":      True,
    }


def get_cnn_info() -> dict:
    return {**_cnn_meta, "model_path": CNN_MODEL_PATH,
            "ready": _cnn_model is not None}
