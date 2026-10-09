"""Exercise every watchdog decision branch WITHOUT touching the live bot.

The dangerous failure mode is not "fails to restart" - it is "restarts
something that was stopped on purpose", e.g. fighting the SURVIVAL drawdown
halt and putting the bot back to work mid-bleed. Test that hardest.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "wd", r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\watchdog.py")
wd = importlib.util.module_from_spec(spec)
sys.modules["wd"] = wd
spec.loader.exec_module(wd)

restarts = []
wd.start_backend = lambda: (restarts.append("backend"), True)[1]
wd.start_session = lambda: (restarts.append("session"), True)[1]
wd.log = lambda m: None

CASES = [
    # (label, port_open, status dict, expect_restart, expect_exit)
    ("healthy, running",
     True, {"running": True, "session_start": "x", "trades_today": 3}, False, 0),
    ("MANUAL STOP - must not restart",
     True, {"running": False, "stopped_reason": "Manual stop"}, False, 0),
    ("DAILY LOSS HALT - must not restart",
     True, {"running": False,
            "stopped_reason": "Daily loss limit hit: -5.2% (limit: -5.0%)"}, False, 0),
    ("SURVIVAL HALT - must not restart",
     True, {"running": False,
            "stopped_reason": "SURVIVAL drawdown halt: equity $96.00"}, False, 0),
    ("DAILY TRADE LIMIT - must not restart",
     True, {"running": False, "stopped_reason": "Daily trade limit hit: 11"}, False, 0),
    ("idle, no reason - report only, do not act",
     True, {"running": False, "stopped_reason": None}, False, 0),
    ("port open but /status silent - do not act",
     True, None, False, 0),
    ("CRASHED - port dead - must restart",
     False, None, True, 2),
]

print(f"{'case':46s} {'restarted':>10s} {'expected':>9s} {'exit':>5s} {'':>6s}")
fails = 0
for label, popen, status, want_restart, want_exit in CASES:
    restarts.clear()
    wd.port_is_open = lambda *a, **k: popen
    wd.get_status = lambda *a, **k: status
    sys.argv = ["watchdog.py"]
    code = wd.main()
    did = bool(restarts)
    ok = (did == want_restart) and (code == want_exit)
    fails += 0 if ok else 1
    print(f"{label:46s} {str(did):>10s} {str(want_restart):>9s} {code:5d} "
          f"{'ok' if ok else 'FAIL':>6s}")

print("\n--- kill switch ---")
import os
KILL = wd.DISABLE_FILE
open(KILL, "w").close()
try:
    restarts.clear()
    wd.port_is_open = lambda *a, **k: False       # crashed
    wd.get_status = lambda *a, **k: None
    code = wd.main()
    ok = (not restarts) and code == 4
    fails += 0 if ok else 1
    print(f"{'crashed + watchdog.disabled present':46s} "
          f"{str(bool(restarts)):>10s} {'False':>9s} {code:5d} {'ok' if ok else 'FAIL':>6s}")
finally:
    os.remove(KILL)

print("\n--- dry run on a crashed backend ---")
restarts.clear()
wd.port_is_open = lambda *a, **k: False
wd.get_status = lambda *a, **k: None
sys.argv = ["watchdog.py", "--dry-run"]
code = wd.main()
ok = (not restarts) and code == 0
fails += 0 if ok else 1
print(f"{'--dry-run must never act':46s} {str(bool(restarts)):>10s} "
      f"{'False':>9s} {code:5d} {'ok' if ok else 'FAIL':>6s}")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
