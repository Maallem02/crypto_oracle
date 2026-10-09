"""Restart the backend PROCESS so new code loads, but do NOT start trading.

Real account: the user presses Start in the app. This only stops the session
cleanly, kills the process, and brings a fresh backend up idle.
"""
import importlib.util
import subprocess
import sys
import time

import requests

HERE = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
spec = importlib.util.spec_from_file_location("wd", HERE + r"\watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

# 1. stop the trading session cleanly (sets "Manual stop" - watchdog leaves it)
try:
    r = requests.post(f"{wd.API}/trading/scalping/stop", timeout=10)
    print("session stop:", r.json().get("message"))
except Exception as e:
    print("session stop failed:", e)

# 2. kill the old process
pid = wd.pid_on_port()
print("old backend pid:", pid)
if not wd.kill_backend():
    print("ABORT - could not free the port")
    sys.exit(1)

# 3. start a fresh backend, WITHOUT starting a session
if not wd.start_backend():
    print("ABORT - backend did not come up")
    sys.exit(1)
time.sleep(8)
st = requests.get(f"{wd.API}/trading/scalping/status", timeout=15).json()
print(f"new backend pid: {wd.pid_on_port()}")
print(f"running={st.get('running')}  stopped_reason={st.get('stopped_reason')}")
print(f"peak_equity reloaded from disk: {st.get('peak_equity')}")
