r"""
rebuild_zones.py — Rebuild zone database + retrain zone ML model.

Usage:
    venv\Scripts\python.exe rebuild_zones.py

What it does:
  1. Detect supply/demand zones from 20 000 H1 candles per symbol
     (≈ 2.3 years of data → covers current price levels)
  2. Label every zone touch as bounce / break-through
  3. Retrain LightGBM zone bounce-probability model
  4. Save zone_database.json + zone_model.pkl

MT5 must be open and logged in.
Typical runtime: 60–120 seconds.
"""
import sys
import warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.config import runtime
runtime.port       = 8000
runtime.db_path    = "crypto_oracle_8000.db"
runtime.ml_db_path = "crypto_oracle_ml.db"
runtime.instance   = "zone_rebuild"
runtime.mt5_path   = None

import MetaTrader5 as mt5

if not mt5.initialize():
    print("[ZONE-REBUILD] ERROR: MT5 not connected")
    print("  Open MT5, log in, then re-run this script.")
    sys.exit(1)

acc = mt5.account_info()
if acc:
    print(f"[ZONE-REBUILD] MT5 connected: #{acc.login}  server={acc.server}")
else:
    print("[ZONE-REBUILD] MT5 initialised (demo mode)")

print()
print("=" * 60)
print("  ZONE MEMORY SYSTEM — Full Rebuild")
print("  Symbols : EURUSD, GBPJPY, XAUUSD, BTC, ETH, SOL")
print("  History : 20 000 H1 candles ≈ 2.3 years")
print("=" * 60)
print()

from features.zones.trainer import run_full_pipeline

result = run_full_pipeline(candles=20000, timeframe="1h")

if not result:
    print("\n[ZONE-REBUILD] Pipeline failed — check output above")
    mt5.shutdown()
    sys.exit(1)

print()
print("=" * 60)
print("  REBUILD COMPLETE")
print(f"  Algorithm : {result.get('algorithm', '?')}")
print(f"  Samples   : {result.get('samples', '?')}")
print(f"  Accuracy  : {result.get('accuracy_pct', '?')}%")
print(f"  AUC       : {result.get('auc_pct', '?')}%")
print(f"  Bounce rate in data: {result.get('bounce_rate_pct', '?')}%")
print()
print("  Top features:")
for feat, imp in list(result.get("feature_importance", {}).items())[:5]:
    print(f"    {feat:<22} {imp}")
print()
print("  zone_database.json + zone_model.pkl updated.")
print("  Restart the bot (or POST /zones/reload) to load new zones.")
print("=" * 60)

# Show quick summary of zones per symbol
import json
try:
    with open("zone_database.json") as f:
        db = json.load(f)
    zones = db.get("zones", db)
    print("\n  Zones detected:")
    total_unbroken = 0
    for sym, zlist in zones.items():
        if not isinstance(zlist, list):
            continue
        unbroken = sum(1 for z in zlist if not z.get("broken"))
        total_unbroken += unbroken
        print(f"    {sym:8s}: {len(zlist)} total, {unbroken} active (unbroken)")
    print(f"\n  Total active zones: {total_unbroken}")
except Exception as e:
    print(f"  (could not summarize DB: {e})")

mt5.shutdown()
