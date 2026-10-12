"""Load Exness daily history for the multi-market trend test, safely. Read-only on
the account (market data only). MT5 builds daily candles from its 1-minute
history, so every new market can cost hundreds of MB of disk:
  * markets loaded one at a time, in an order that mixes the asset classes, so a
    partial load is still diversified
  * stops before free disk would fall under FLOOR_GB (MT5 and the bots keep room)
  * pause between markets; stops if the backend stops answering
  * each market's daily candles saved to data/multi_d1/<symbol>.npy (tiny)"""
import os
import shutil
import sys
import time
from datetime import datetime, timezone

import numpy as np
import requests

import MetaTrader5 as mt5

UTC = timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "multi_d1")
FLOOR_GB = 5.0
START = datetime(2017, 10, 1, tzinfo=UTC)
# the pre-defined universe (27 tradable markets), interleaved by class
ORDER = ["US500m", "USOILm", "EURUSDm", "XAUUSDm", "BTCUSDm",
         "DE30m", "UKOILm", "GBPUSDm", "XAGUSDm", "ETHUSDm",
         "JP225m", "XNGUSDm", "USDJPYm", "XCUUSDm",
         "USTECm", "AUDUSDm", "XPTUSDm",
         "UK100m", "USDCADm", "XPDUSDm",
         "US30m", "USDCHFm",
         "AUS200m", "NZDUSDm",
         "HK50m", "FR40m", "STOXX50m"]


def free_gb():
    return shutil.disk_usage("C:\\").free / 1e9


def backend_ok():
    try:
        return requests.get("http://127.0.0.1:8000/trading/scalping/status", timeout=20).status_code == 200
    except requests.RequestException:
        return False


os.makedirs(OUT, exist_ok=True)
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
costs = []
for k, sym in enumerate(ORDER):
    path = os.path.join(OUT, sym + ".npy")
    if os.path.exists(path):
        print(f"{sym:9s} already saved - skipped", flush=True)
        continue
    before = free_gb()
    avg = np.mean(costs) if costs else 0.5
    if before - avg < FLOOR_GB:
        print(f"STOP before {sym}: free {before:.2f} GB, a market costs about {avg:.2f} GB, floor {FLOOR_GB} GB", flush=True)
        break
    mt5.symbol_select(sym, True)
    n_prev, bars, t0 = -1, None, time.time()
    while time.time() - t0 < 120:                     # MT5 downloads in the background: wait until stable
        bars = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, START, now)
        n = 0 if bars is None else len(bars)
        if n > 0 and n == n_prev:
            break
        n_prev = n
        time.sleep(4)
    used = before - free_gb()
    costs.append(max(used, 0.0))
    if bars is None or len(bars) == 0:
        print(f"{sym:9s} NO DATA ({mt5.last_error()})  disk used {used * 1000:.0f} MB", flush=True)
        continue
    np.save(path, bars)
    print(f"{sym:9s} {len(bars):5d} daily bars from {datetime.fromtimestamp(int(bars['time'][0]), UTC):%d/%m/%Y}"
          f"  disk used {used * 1000:5.0f} MB  free now {free_gb():.2f} GB", flush=True)
    time.sleep(3)
    if (k + 1) % 5 == 0 and not backend_ok():
        print("STOP: the backend did not answer - leaving MT5 alone", flush=True)
        break
mt5.shutdown()
print(f"saved {len([f for f in os.listdir(OUT) if f.endswith('.npy')])} markets, free disk {free_gb():.2f} GB, "
      f"backend {'OK' if backend_ok() else 'NOT answering'}")
