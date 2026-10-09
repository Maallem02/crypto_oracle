"""Restart the backend so new code loads, and put the SMC session back exactly
as it was (running -> running, stopped -> stopped). DEMO ONLY: refuses to start
a session on a real account."""
import importlib.util
import sys
import time

import MetaTrader5 as mt5
import requests

HERE = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
spec = importlib.util.spec_from_file_location("wd", HERE + r"\watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

st = requests.get(f"{wd.API}/trading/scalping/status", timeout=15).json()
was_running = bool(st.get("running"))
vp = requests.get(f"{wd.API}/trading/vp/status", timeout=30).json()
print(f"before: SMC running={was_running}  VP enabled={vp.get('enabled')}  "
      f"VP open={len(vp.get('open_positions') or [])}")

mt5.initialize(timeout=10000)
a = mt5.account_info()
mt5.shutdown()
demo = a is not None and a.trade_mode == 0
print(f"account {a.login if a else '?'} {'DEMO' if demo else 'REAL/unknown'}")

if not wd.kill_backend():
    sys.exit("ABORT - could not free the port")
if not wd.start_backend():
    sys.exit("ABORT - backend did not come up")
time.sleep(8)
if was_running:
    if demo:
        wd.start_session()
    else:
        print("REAL account: session NOT restarted - press Start in the app")
st = requests.get(f"{wd.API}/trading/scalping/status", timeout=15).json()
vp = requests.get(f"{wd.API}/trading/vp/status", timeout=30).json()
print(f"after:  SMC running={st.get('running')}  VP enabled={vp.get('enabled')}  "
      f"max_per_symbol={vp['settings'].get('max_per_symbol')}  "
      f"same_direction={vp['settings'].get('same_direction')}  last_error={vp.get('last_error')}")
