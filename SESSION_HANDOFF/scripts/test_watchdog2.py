"""Watchdog branch tests, now including STALL detection.

The stall path kills a LIVE process, so the false-positive cases matter more
than the true-positive one. Specifically it must NOT fire for:
  - a bot stopped on purpose (no scans expected)
  - a backend that just booted (no scan yet, legitimately)
  - a scan that is merely a few minutes old
  - a second time inside the cooldown window
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta

spec = importlib.util.spec_from_file_location(
    "wd", r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\watchdog.py")
wd = importlib.util.module_from_spec(spec)
sys.modules["wd"] = wd
spec.loader.exec_module(wd)

acted = []
wd.start_backend = lambda: (acted.append("backend"), True)[1]
wd.start_session = lambda: (acted.append("session"), True)[1]
wd.kill_backend = lambda: (acted.append("kill"), True)[1]
wd.log = lambda m: None

# keep the real state file out of the way
wd.STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wd_state_test.json")


def ago(mins):
    return (datetime.now() - timedelta(minutes=mins)).isoformat()


def clear_state():
    if os.path.exists(wd.STATE):
        os.remove(wd.STATE)


CASES = [
    # label, port_open, status, expect_action, expect_exit
    ("healthy, scanning 1 min ago", True,
     {"running": True, "last_scan": ago(1), "session_start": ago(300)}, False, 0),
    ("scan 10 min old - under limit, leave", True,
     {"running": True, "last_scan": ago(10), "session_start": ago(300)}, False, 0),
    ("STALLED: scan 40 min old", True,
     {"running": True, "last_scan": ago(40), "session_start": ago(300)}, True, 2),
    ("STALLED: 3h30 like the real 09-16 event", True,
     {"running": True, "last_scan": ago(210), "session_start": ago(600)}, True, 2),
    ("just booted, no scan yet - leave", True,
     {"running": True, "last_scan": None, "session_start": ago(2)}, False, 0),
    ("booted 30 min ago, still no scan - STALLED", True,
     {"running": True, "last_scan": None, "session_start": ago(30)}, True, 2),
    ("MANUAL STOP with ancient scan - must NOT restart", True,
     {"running": False, "stopped_reason": "Manual stop",
      "last_scan": ago(999), "session_start": ago(999)}, False, 0),
    ("SURVIVAL HALT with ancient scan - must NOT restart", True,
     {"running": False, "stopped_reason": "SURVIVAL drawdown halt: equity $96",
      "last_scan": ago(999), "session_start": ago(999)}, False, 0),
    ("DAILY LOSS HALT with ancient scan - must NOT restart", True,
     {"running": False, "stopped_reason": "Daily loss limit hit: -5.2%",
      "last_scan": ago(999), "session_start": ago(999)}, False, 0),
    ("malformed last_scan - treat as no scan, session old", True,
     {"running": True, "last_scan": "not-a-date", "session_start": ago(300)}, True, 2),
    ("CRASHED - port dead", False, None, True, 2),
    ("port open, /status silent", True, None, False, 0),
]

print(f"{'case':52s} {'acted':>7s} {'want':>6s} {'exit':>5s} {'':>6s}")
fails = 0
for label, popen, status, want_action, want_exit in CASES:
    clear_state()
    acted.clear()
    wd.port_is_open = lambda *a, **k: popen
    wd.get_status = lambda *a, **k: status
    sys.argv = ["watchdog.py"]
    code = wd.main()
    did = bool(acted)
    ok = (did == want_action) and (code == want_exit)
    fails += 0 if ok else 1
    print(f"{label:52s} {str(did):>7s} {str(want_action):>6s} {code:5d} "
          f"{'ok' if ok else 'FAIL':>6s}")

print("\n--- anti-loop: second stall restart inside the cooldown ---")
clear_state()
stalled = {"running": True, "last_scan": ago(40), "session_start": ago(300)}
wd.port_is_open = lambda *a, **k: True
wd.get_status = lambda *a, **k: stalled
sys.argv = ["watchdog.py"]
acted.clear()
c1 = wd.main()
first = list(acted)
acted.clear()
c2 = wd.main()
second = list(acted)
ok = bool(first) and not second and c2 == 3
fails += 0 if ok else 1
print(f"{'1st run restarts, 2nd run refuses':52s} "
      f"{str(bool(second)):>7s} {'False':>6s} {c2:5d} {'ok' if ok else 'FAIL':>6s}")

print("\n--- dry run must never act on a stall ---")
clear_state()
acted.clear()
sys.argv = ["watchdog.py", "--dry-run"]
c = wd.main()
ok = (not acted) and c == 0
fails += 0 if ok else 1
print(f"{'--dry-run on a stalled bot':52s} {str(bool(acted)):>7s} "
      f"{'False':>6s} {c:5d} {'ok' if ok else 'FAIL':>6s}")

print("\n--- kill switch beats a stall ---")
clear_state()
open(wd.DISABLE_FILE, "w").close()
try:
    acted.clear()
    sys.argv = ["watchdog.py"]
    c = wd.main()
    ok = (not acted) and c == 4
    fails += 0 if ok else 1
    print(f"{'stalled + watchdog.disabled':52s} {str(bool(acted)):>7s} "
          f"{'False':>6s} {c:5d} {'ok' if ok else 'FAIL':>6s}")
finally:
    os.remove(wd.DISABLE_FILE)
    clear_state()

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
