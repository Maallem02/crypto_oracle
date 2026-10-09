"""Deploy the independent VP bot on the DEMO account and prove, live, that
stopping either bot leaves the other running. Ends with BOTH running."""
import importlib.util
import sys
import time
from datetime import datetime

import MetaTrader5 as mt5
import requests

HERE = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
spec = importlib.util.spec_from_file_location("wd", HERE + r"\watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)
API = f"{wd.API}/trading"

mt5.initialize(timeout=10000)
a = mt5.account_info()
mt5.shutdown()
print(f"account {a.login} {'DEMO' if a.trade_mode == 0 else 'REAL'}")
if a.trade_mode != 0:
    print("ABORT - not the demo account")
    sys.exit(1)


def smc():
    s = requests.get(f"{API}/scalping/status", timeout=10).json()
    return s.get("running"), s.get("last_scan")


def vp():
    s = requests.get(f"{API}/vp/status", timeout=10).json()
    return s.get("enabled"), s.get("last_scan"), s.get("last_error")


# 1. restart so the new code loads (watchdog brings SMC back up)
try:
    requests.post(f"{API}/scalping/stop", timeout=10)
except Exception:
    pass
wd.kill_backend()
if not wd.start_backend() or not wd.start_session():
    print("ABORT - restart failed")
    sys.exit(1)
time.sleep(75)
print(f"\nafter restart     SMC running/last_scan={smc()}   VP enabled/last_scan/err={vp()}")

# 2. stop SMC -> VP must keep scanning
requests.post(f"{API}/scalping/stop", timeout=10)
v0 = vp()[1]
time.sleep(75)
s1, v1 = smc(), vp()
print(f"SMC stopped       SMC={s1}   VP={v1}")
print(f"  -> VP kept scanning while SMC was stopped: {v1[1] != v0 and v1[0]}")

# 3. stop VP, restart SMC -> SMC must scan while VP stays frozen
requests.post(f"{API}/vp/stop", timeout=10)
time.sleep(3)
v_frozen = vp()[1]
wd.start_session()
s0 = smc()[1]
time.sleep(75)
s2, v2 = smc(), vp()
print(f"VP stopped        SMC={s2}   VP={v2}")
print(f"  -> SMC scanned while VP was stopped: {s2[0] and s2[1] != s0}")
print(f"  -> VP stayed stopped (no new scan):  {v2[1] == v_frozen and not v2[0]}")

# 4. back to normal: both running
requests.post(f"{API}/vp/start", timeout=10)
time.sleep(75)
print(f"\nFINAL             SMC={smc()}   VP={vp()}")
