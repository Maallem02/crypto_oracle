"""Post-deploy check for the Volume Profile strategy. Read-only."""
import os
import sqlite3
from datetime import datetime

import requests

HERE = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
API = "http://127.0.0.1:8000/trading"

st = requests.get(f"{API}/scalping/status", timeout=10).json()
ls = st.get("last_scan")
age = (datetime.now() - datetime.fromisoformat(ls)).total_seconds() / 60 if ls else None
print(f"bot running={st.get('running')}  session_start={st.get('session_start')}  "
      f"last scan {f'{age:.1f} min ago' if age is not None else 'none'}")

logs = sorted([f for f in os.listdir(HERE) if f.startswith("backend_8000_") and f.endswith(".log")],
              key=lambda f: os.path.getmtime(os.path.join(HERE, f)))
txt = open(os.path.join(HERE, logs[-1]), encoding="utf-8", errors="replace").read().splitlines()
errs = [l for l in txt if "[VP] scan error" in l or ("Traceback" in l)]
print(f"VP errors in {logs[-1]}: {len(errs)}")
for l in errs[:5]:
    print("   ", l[:150])

db = sqlite3.connect(os.path.join(HERE, "crypto_oracle_8000.db"))
db.row_factory = sqlite3.Row
print("\nvp_log (separate journal):")
for r in db.execute("SELECT timestamp, symbol, setup, status, reason FROM vp_log ORDER BY id"):
    print(f"  {r['timestamp'][:19]}  {r['symbol']:7s} {r['setup']:12s} {r['status']:9s} {r['reason'][:80]}")

start = st.get("session_start")
smc = db.execute("SELECT COUNT(*) FROM rejection_log WHERE timestamp >= ?", (start,)).fetchone()[0]
vp_in_smc = db.execute("SELECT COUNT(*) FROM scalp_log WHERE data LIKE '%volume_profile%' "
                       "OR data LIKE '%va_breakout%' OR data LIKE '%poc_bounce%'").fetchone()[0]
print(f"\nSMC still scanning: {smc} SMC evaluations logged since the restart")
print(f"VP rows that leaked into the SMC journal (scalp_log): {vp_in_smc}")

print("\n/scalping/vp-stats:")
s = requests.get(f"{API}/scalping/vp-stats", timeout=10).json()
print(f"  strategy={s['strategy']}  magic={s['magic']}  all={s['all']}")
