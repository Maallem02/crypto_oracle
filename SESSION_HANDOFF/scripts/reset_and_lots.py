"""User request (real account):
  - reset the survival-halt peak to today's equity (~$52.45)
  - lot sizes: ETH 0.38, BTC 0.02

The backend holds peak_equity IN MEMORY, so editing risk_state.json while it
runs would be ignored (and could be overwritten). Order:
  1. persist the new lot sizes (PATCH, full dict - PATCH replaces the key)
  2. stop the backend
  3. write the new peak into risk_state.json
  4. start the backend IDLE - it reloads both from disk
Trading stays stopped: the user presses Start in the app.
"""
import importlib.util
import json
import os
import sys

import requests

HERE = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
RS = os.path.join(HERE, "features", "trading", "risk_state.json")
spec = importlib.util.spec_from_file_location("wd", HERE + r"\watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

import MetaTrader5 as mt5
mt5.initialize(timeout=10000)
a = mt5.account_info()
pos = mt5.positions_get() or []
mt5.shutdown()
print(f"account {a.login}  equity ${a.equity:.2f}  balance ${a.balance:.2f}  "
      f"open positions {len(pos)}")
if pos:
    print("ABORT - positions are open; equity is not a clean baseline")
    sys.exit(1)

# 1. lot sizes - send the FULL dict, PATCH replaces lot_sizes wholesale
st = requests.get(f"{wd.API}/trading/scalping/status", timeout=10).json()
lots = dict(st["settings"].get("lot_sizes", {}))
print(f"lot sizes before: {lots}")
lots["ETH"] = 0.38
lots["BTC"] = 0.02
r = requests.patch(f"{wd.API}/trading/scalping/settings",
                   json={"lot_sizes": lots}, timeout=10).json()
print(f"lot sizes after:  {r['settings']['lot_sizes']}")

# 2. stop the backend (session is already stopped)
if not wd.kill_backend():
    print("ABORT - could not stop the backend")
    sys.exit(1)

# 3. new peak = today's equity
state = json.load(open(RS, encoding="utf-8"))
old = state.get("peak_equity")
state["peak_equity"] = round(a.equity, 2)
state["login"] = a.login
with open(RS, "w", encoding="utf-8") as fh:
    json.dump(state, fh, indent=2)
print(f"peak_equity: ${old} -> ${state['peak_equity']}  "
      f"(halt now fires at ${state['peak_equity'] * 0.65:.2f})")

# 4. backend back up, IDLE
if not wd.start_backend():
    print("ABORT - backend did not come up")
    sys.exit(1)
import time
time.sleep(8)
st = requests.get(f"{wd.API}/trading/scalping/status", timeout=15).json()
print(f"\nreloaded: running={st.get('running')}  peak_equity={st.get('peak_equity')}")
print(f"          lot_sizes={st['settings'].get('lot_sizes')}")
print(f"          max_daily_loss_pct={st['settings'].get('max_daily_loss_pct')}")
