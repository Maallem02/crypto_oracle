"""
train_cnn.py — Train the 1D CNN on historical candle sequences.

Usage:
    venv\Scripts\python.exe train_cnn.py

Requirements:
  • MT5 must be open and logged into the broker account (to fetch historical candles).
  • At least 40 completed trades in trade_signals with outcome != NULL.

What it does:
  1. For every closed trade in trade_signals, fetches the 50×1h candles
     that existed BEFORE that trade opened.
  2. Normalises each window to [0, 1] (symbol-agnostic price patterns).
  3. Trains a 4-layer 1D CNN:  Conv→Conv→MaxPool→Conv→Conv→MaxPool→GAP→Dense(3)
  4. Saves the trained model to  cnn_model.pt
  5. The model is then auto-loaded by predict_decision() as a secondary gate.

If MT5 cannot be connected, the script will report how many sequences were
collected and exit — re-run when MT5 is available.
"""
import sys
import warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Bootstrap (same as main.py) ───────────────────────────────────────────────
from core.config import runtime
runtime.port       = 8000
runtime.db_path    = "crypto_oracle_8000.db"
runtime.ml_db_path = "crypto_oracle_ml.db"
runtime.instance   = "cnn_train"
runtime.mt5_path   = None

import MetaTrader5 as mt5

# Try to connect MT5
if not mt5.initialize():
    print("[CNN] WARNING: MT5 not connected — candles may be unavailable")
    print("[CNN] Open MT5 and log in, then re-run this script")
else:
    acc = mt5.account_info()
    if acc:
        print(f"[CNN] MT5 connected: #{acc.login}  server={acc.server}")
    else:
        print("[CNN] MT5 initialised (demo/offline mode)")

# ── Train ──────────────────────────────────────────────────────────────────────
from features.trading.cnn_model import train_cnn, get_cnn_info

print("\n" + "="*60)
print("  CNN TRAINING — 1D Conv on 50×1h OHLCV candles")
print("="*60)

result = train_cnn()

if result is None:
    print("\n[CNN] Training failed — check output above for details")
    sys.exit(1)

print("\n" + "="*60)
print("  TRAINING COMPLETE")
print(f"  Samples   : {result['samples']}")
print(f"  Accuracy  : {result['accuracy_pct']}%")
print(f"  AUC       : {result['auc_pct']}%")
print(f"  Classes   : {result['class_distribution']}")
print(f"  Saved     : cnn_model.pt")
print("="*60)
print("\nThe CNN is now active as a secondary gate in predict_decision().")
print("Restart the bot to load the new model.\n")

mt5.shutdown()
