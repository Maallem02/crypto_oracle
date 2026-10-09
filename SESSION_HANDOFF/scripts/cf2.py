"""Size the coupling: how much silver did gold's breakers keep out?

Removing gold removes 4 of the 5 metals breaker windows this week. In those
windows silver was blocked; without gold it would have traded. Silver ran at
-$1.22/trade this week, so those extra trades likely SUBTRACT from the naive
+$13.03.

Count the silver signals actually rejected by the breaker inside the
gold-caused windows. That converts a hand-wave into a number.
"""
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# windows that exist ONLY because gold lost (from the previous replay)
GOLD_CAUSED = [
    ("sell", datetime(2026, 9, 15, 17, 22), datetime(2026, 9, 15, 21, 22)),
    ("sell", datetime(2026, 9, 15, 19, 14), datetime(2026, 9, 15, 23, 14)),
    ("sell", datetime(2026, 9, 18, 6, 45), datetime(2026, 9, 18, 10, 45)),
    ("buy",  datetime(2026, 9, 18, 16, 40), datetime(2026, 9, 18, 20, 40)),
]

print("=" * 96)
print("ALL BREAKER REJECTIONS THIS WEEK, BY SYMBOL")
print("=" * 96)
rows = list(db.execute(
    "select timestamp, symbol, reason from rejection_log "
    "where timestamp >= '2026-09-14' and reason like '%breaker%'"))
c = Counter((r["symbol"] or "?").upper() for r in rows)
for k, v in c.most_common():
    print(f"  {k:10s} {v:5d} breaker rejections")
print(f"  {'TOTAL':10s} {len(rows):5d}")

print("\n" + "=" * 96)
print("SILVER SIGNALS BLOCKED BY THE BREAKER INSIDE GOLD-CAUSED WINDOWS")
print("=" * 96)
sil = [r for r in rows if "XAG" in (r["symbol"] or "").upper()]
print(f"  silver breaker rejections all week: {len(sil)}")
hit = 0
for d, a, b in GOLD_CAUSED:
    inside = [r for r in sil
              if a <= datetime.fromisoformat(r["timestamp"]) <= b]
    hit += len(inside)
    print(f"    window {d:4s} {a:%a %m-%d %H:%M}-{b:%H:%M}: "
          f"{len(inside):4d} silver signals blocked")
print(f"\n  total silver signals blocked by gold-caused breakers: {hit}")

print("\n" + "=" * 96)
print("HOW MANY DISTINCT SILVER OPPORTUNITIES IS THAT?")
print("=" * 96)
print("  Rejections are per scan (every 3 min) x per timeframe, so the raw")
print("  count massively overstates distinct opportunities. Collapse to")
print("  distinct scan minutes, then to contiguous episodes.\n")
for d, a, b in GOLD_CAUSED:
    inside = sorted({r["timestamp"][:16] for r in sil
                     if a <= datetime.fromisoformat(r["timestamp"]) <= b})
    if not inside:
        print(f"    {d:4s} {a:%a %m-%d %H:%M}: no silver signals blocked")
        continue
    times = [datetime.fromisoformat(t) for t in inside]
    episodes = 1
    for i in range(1, len(times)):
        if (times[i] - times[i-1]).total_seconds() > 1800:
            episodes += 1
    print(f"    {d:4s} {a:%a %m-%d %H:%M}: {len(inside):3d} distinct minutes, "
          f"~{episodes} episode(s)")

print("\n" + "=" * 96)
print("VERDICT")
print("=" * 96)
print("  silver this week: 10 trades, -$12.15, avg -$1.22/trade, PF 0.78")
print("  every extra silver trade is worth about -$1.22 in expectation.")
