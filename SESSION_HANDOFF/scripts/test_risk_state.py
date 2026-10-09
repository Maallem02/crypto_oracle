"""Tests for the persistent risk state. Never touches the live state file or
the live settings file: both are redirected / mocked.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.router as r

SCRATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "risk_state_test.json")
r._RISK_STATE_FILE = SCRATCH
r._persist_settings = lambda d: None          # never overwrite real settings

fails = 0


def check(label, cond):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}")


def reset(state=None):
    if os.path.exists(SCRATCH):
        os.remove(SCRATCH)
    r.risk_state.clear()
    r.risk_state.update(state or {})
    r.scalp_state.pop("peak_equity", None)
    if state and state.get("peak_equity"):
        r.scalp_state["peak_equity"] = state["peak_equity"]


def fake_account(login, equity):
    r.mt5 = SimpleNamespace(account_info=lambda: SimpleNamespace(
        login=login, equity=equity, balance=equity))


settings = r.ScalpingSettings(**r._load_saved_settings())

print("=" * 80)
print("1. ACCOUNT BINDING - the demo -> real trap")
print("=" * 80)
reset({"login": 111, "peak_equity": 228.74,
       "day_anchor": datetime.now().isoformat()})
r._bind_risk_state_to_account(111, 180.0)
check("same account: peak kept at 228.74", r.scalp_state["peak_equity"] == 228.74)
r._bind_risk_state_to_account(999, 70.0)
check("new account: peak resets to 70.00", r.scalp_state["peak_equity"] == 70.0)
check("new account: login recorded", r.risk_state["login"] == 999)
dd = (70.0 - r.scalp_state["peak_equity"]) / r.scalp_state["peak_equity"] * 100
check(f"new account: drawdown is {dd:.0f}% (not -69%) -> no instant halt", dd == 0)
check("new account: written to disk",
      json.load(open(SCRATCH))["login"] == 999)

print("\n" + "=" * 80)
print("2. DAILY WINDOW")
print("=" * 80)
midnight = datetime.combine(datetime.now().date(), datetime.min.time())
reset({"day_anchor": (midnight - timedelta(days=2)).isoformat()})
check("anchor from 2 days ago -> window starts at midnight today",
      r._daily_window_start() == midnight.isoformat())
later = midnight + timedelta(hours=14)
reset({"day_anchor": later.isoformat()})
check("anchor today 14:00 -> window starts 14:00",
      r._daily_window_start() == later.isoformat())
reset({})
check("no anchor -> window starts at midnight",
      r._daily_window_start() == midnight.isoformat())

print("\n" + "=" * 80)
print("3. /scalping/start - watchdog vs app")
print("=" * 80)
old_anchor = (midnight + timedelta(hours=3)).isoformat()
reset({"login": 111, "peak_equity": 200.0, "day_anchor": old_anchor})
fake_account(111, 180.0)                    # -10%, above the halt threshold
r.start_scalping(settings, source="watchdog")
check("watchdog: peak kept (200)", r.scalp_state["peak_equity"] == 200.0)
check("watchdog: daily anchor untouched", r.risk_state["day_anchor"] == old_anchor)

reset({"login": 111, "peak_equity": 200.0, "day_anchor": old_anchor})
fake_account(111, 180.0)
r.start_scalping(settings)                  # default = app
check("app, -10%: peak KEPT (changing a setting must not erase protection)",
      r.scalp_state["peak_equity"] == 200.0)
check("app: daily anchor moved to now", r.risk_state["day_anchor"] != old_anchor)

reset({"login": 111, "peak_equity": 200.0, "day_anchor": old_anchor})
fake_account(111, 120.0)                    # -40%, past the halt threshold
r.start_scalping(settings)
check("app, -40%: peak reset to 120 so the bot can resume",
      r.scalp_state["peak_equity"] == 120.0)

reset({"login": 111, "peak_equity": 200.0, "day_anchor": old_anchor})
fake_account(111, 120.0)
r.start_scalping(settings, source="watchdog")
check("watchdog, -40%: peak KEPT -> bot re-halts after a crash",
      r.scalp_state["peak_equity"] == 200.0)

print("\n" + "=" * 80)
print("4. SURVIVES A 'RESTART' (reload from disk)")
print("=" * 80)
reset({"login": 111, "peak_equity": 173.07, "day_anchor": old_anchor})
r._save_risk_state(r.risk_state)
r.risk_state.clear()
reloaded = r._load_risk_state()
check("peak 173.07 read back from disk", reloaded.get("peak_equity") == 173.07)
check("login read back from disk", reloaded.get("login") == 111)

if os.path.exists(SCRATCH):
    os.remove(SCRATCH)
print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
