"""Restart the backend so it re-reads .env (now the DEMO login), then start
trading ONLY if MT5 is confirmed on the demo account.

The running backend loaded .env on Wed 19:11, when it held the REAL login, and
its ensure_mt5() re-logs the terminal to that login on every call - which is
why switching MT5 to demo by hand kept flipping back to real.
"""
import importlib.util
import sys
import time

import requests

HERE = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
DEMO = 262890622
spec = importlib.util.spec_from_file_location("wd", HERE + r"\watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

import MetaTrader5 as mt5


def terminal_account():
    mt5.initialize(timeout=10000)
    a = mt5.account_info()
    mt5.shutdown()
    return (a.login, a.server, a.trade_mode, a.balance, a.equity) if a else None


print("before:", terminal_account())

# 1. stop the old backend (it is the thing forcing the real login)
try:
    requests.post(f"{wd.API}/trading/scalping/stop", timeout=10)
except Exception:
    pass
if not wd.kill_backend():
    print("ABORT - could not stop the old backend")
    sys.exit(1)

# 2. new backend, idle. Its position manager runs every 30 s and calls
#    ensure_mt5() with the CURRENT .env -> should log MT5 into the demo.
if not wd.start_backend():
    print("ABORT - new backend did not come up")
    sys.exit(1)

acc = None
for i in range(12):                  # up to ~2 min
    time.sleep(10)
    acc = terminal_account()
    print(f"  t+{10*(i+1):3d}s  terminal on: {acc}")
    if acc and acc[0] == DEMO:
        break

if not acc or acc[0] != DEMO:
    print("\nNOT ON DEMO - leaving trading STOPPED. Nothing started.")
    sys.exit(2)

# 3. confirmed demo -> start the bot
started = wd.start_session()
time.sleep(70)                       # let the first scans run
st = requests.get(f"{wd.API}/trading/scalping/status", timeout=10).json()
print(f"\nrunning={st.get('running')}  last_scan={st.get('last_scan')}  "
      f"peak_equity={st.get('peak_equity')}")
print(f"lot_sizes={st['settings'].get('lot_sizes')}")
print("after 70 s the terminal is still on:", terminal_account())
