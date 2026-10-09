"""How much of the tradeable market has the bot actually been AWAKE for?

Every tuning study I have run is measured on a bot that is only running part of
the time. If uptime is the dominant term, no gate or trail setting matters
until it is fixed.

rejection_log gets a row on every scan, so a gap in its timestamps IS downtime.
Tradeable time = weekdays only (the weekend block is intentional, not an outage).
"""
import sqlite3
from datetime import datetime, timedelta

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
START = datetime(2026, 8, 25)
NOW = datetime.now()

ts = [datetime.fromisoformat(r[0]) for r in db.execute(
    "select distinct timestamp from rejection_log where timestamp >= ? "
    "order by timestamp", (START.isoformat(),))]
print(f"scan timestamps since {START:%Y-%m-%d}: {len(ts)}\n")

# weekday seconds in a window (weekend block is by design, not downtime)
def weekday_seconds(a, b):
    tot = 0.0
    cur = a
    while cur < b:
        nxt = min(b, (cur + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0))
        if cur.weekday() < 5:
            tot += (nxt - cur).total_seconds()
        cur = nxt
    return tot

total_tradeable = weekday_seconds(START, NOW)

# downtime = gaps > 20 min, counted only over weekday time
GAP = timedelta(minutes=20)
outages = []
for i in range(1, len(ts)):
    if ts[i] - ts[i - 1] > GAP:
        outages.append((ts[i - 1], ts[i]))
if ts and NOW - ts[-1] > GAP:
    outages.append((ts[-1], NOW))

down = sum(weekday_seconds(a, b) for a, b in outages)

print("=" * 86)
print("OUTAGES OVER 20 MIN (weekday hours only)")
print("=" * 86)
for a, b in outages:
    h = weekday_seconds(a, b) / 3600
    if h < 0.35:
        continue
    print(f"  {a:%a %m-%d %H:%M}  ->  {b:%a %m-%d %H:%M}   {h:6.1f} tradeable h")

print("\n" + "=" * 86)
print("UPTIME")
print("=" * 86)
print(f"  tradeable weekday hours since {START:%m-%d}:  {total_tradeable/3600:8.1f} h")
print(f"  hours DOWN:                             {down/3600:8.1f} h")
print(f"  hours UP:                               {(total_tradeable-down)/3600:8.1f} h")
print(f"  UPTIME:                                 {100*(1-down/total_tradeable):8.1f} %")

# what did that cost in trade opportunities?
n_sig = db.execute("select count(*) from scalp_log where timestamp >= ?",
                   (START.isoformat(),)).fetchone()[0]
up_h = (total_tradeable - down) / 3600
rate = n_sig / up_h if up_h else 0
print(f"\n  trades taken while up: {n_sig}   rate {rate:.2f}/tradeable-hour")
print(f"  trades NOT taken during downtime (est): {rate*down/3600:.0f}")
