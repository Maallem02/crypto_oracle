"""What is the RUNNING process actually using, and is it really scanning?"""
import sqlite3
from datetime import datetime, timezone

import requests

for base in ("http://127.0.0.1:8000", "http://127.0.0.1:8000/api",
             "http://127.0.0.1:8000/trading", "http://127.0.0.1:8000/api/trading"):
    try:
        r = requests.get(f"{base}/scalping/status", timeout=5)
        if r.status_code == 200:
            print(f"LIVE STATUS via {base}/scalping/status")
            d = r.json()
            for k in ("running", "session_start", "trades_today", "daily_pnl",
                      "last_scan", "paused_symbols", "stopped_reason"):
                if k in d:
                    print(f"  {k:16s} {d[k]}")
            s = d.get("settings") or {}
            for k in ("risk_percent", "target_risk_pct", "cooldown_minutes",
                      "trail_start_r", "be_trigger_r", "max_trades",
                      "min_score", "enabled_symbols"):
                if k in s:
                    print(f"  settings.{k:14s} {s[k]}")
            break
    except Exception as e:
        print(f"  {base} -> {type(e).__name__}")
else:
    print("no /scalping/status route answered")

print(f"\nlocal now  {datetime.now():%Y-%m-%d %H:%M:%S}")
print(f"utc now    {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}  "
      f"weekday={datetime.now(timezone.utc).weekday()} (5,6 = blocked)")

db = sqlite3.connect(
    r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
print("\nlast 8 rejection_log rows (proof the scan loop is alive):")
for r in db.execute("select timestamp,symbol,reason from rejection_log "
                    "order by id desc limit 8"):
    print(f"  {r['timestamp']}  {r['symbol']:8s} {r['reason']}")

print("\nrejection reasons in the LAST HOUR:")
from collections import Counter
c = Counter()
cutoff = datetime.now().replace(microsecond=0).isoformat()[:13]  # this hour
for r in db.execute("select symbol,reason from rejection_log "
                    "where timestamp >= ? ", (cutoff,)):
    c[f"{r['symbol']:8s} {str(r['reason']).split(':')[0][:40]}"] += 1
for k, v in c.most_common(12):
    print(f"  {v:5d}  {k}")
if not c:
    print("  (none this hour)")
