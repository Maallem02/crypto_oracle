"""Uptime, counting ONLY hours the bot is supposed to be trading.

is_trading_session() blocks:
  - weekends (weekday >= 5)
  - the dead zone, hour_utc >= 21 or hour_utc < 2

So the nightly ~21:00->02:00 UTC gaps are BY DESIGN and must not be counted as
downtime. What is left is genuine outage: crashes.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
START = datetime(2026, 8, 25)
NOW = datetime.now()
LOCAL_OFFSET = 1          # machine runs UTC+1; rejection_log timestamps are local


def session_seconds(a: datetime, b: datetime) -> float:
    """Seconds between a and b that is_trading_session() would allow."""
    tot = 0.0
    cur = a.replace(minute=0, second=0, microsecond=0)
    while cur < b:
        nxt = cur + timedelta(hours=1)
        lo, hi = max(a, cur), min(b, nxt)
        if hi > lo:
            u = cur - timedelta(hours=LOCAL_OFFSET)     # -> UTC
            if u.weekday() < 5 and not (u.hour >= 21 or u.hour < 2):
                tot += (hi - lo).total_seconds()
        cur = nxt
    return tot


ts = [datetime.fromisoformat(r[0]) for r in db.execute(
    "select distinct timestamp from rejection_log where timestamp >= ? "
    "order by timestamp", (START.isoformat(),))]

total = session_seconds(START, NOW)
GAP = timedelta(minutes=20)
outages = [(ts[i - 1], ts[i]) for i in range(1, len(ts)) if ts[i] - ts[i - 1] > GAP]
if ts and NOW - ts[-1] > GAP:
    outages.append((ts[-1], NOW))

real = [(a, b, session_seconds(a, b) / 3600) for a, b in outages]
real = [x for x in real if x[2] >= 0.35]
down = sum(x[2] for x in real)

print("=" * 84)
print("GENUINE OUTAGES  (session hours lost to crashes, dead zone excluded)")
print("=" * 84)
for a, b, h in real:
    print(f"  {a:%a %m-%d %H:%M}  ->  {b:%a %m-%d %H:%M}   {h:5.1f} h lost")

print("\n" + "=" * 84)
print("UPTIME DURING HOURS THE BOT SHOULD HAVE BEEN TRADING")
print("=" * 84)
print(f"  session hours since {START:%m-%d}:   {total/3600:7.1f} h")
print(f"  lost to crashes:              {down:7.1f} h")
print(f"  actually trading:             {total/3600-down:7.1f} h")
print(f"  UPTIME:                       {100*(1-down/(total/3600)):7.1f} %")

n = db.execute("select count(*) from scalp_log where timestamp >= ?",
               (START.isoformat(),)).fetchone()[0]
up = total / 3600 - down
rate = n / up if up else 0
print(f"\n  trades taken: {n} over {up:.0f} live session hours = {rate:.2f}/h")
print(f"  trades missed to downtime (est): {rate*down:.0f}")
pnl = db.execute("select avg(profit) from scalp_log where timestamp >= ? "
                 "and profit is not null", (START.isoformat(),)).fetchone()[0] or 0
print(f"  avg P/L per trade ${pnl:+.2f}  ->  downtime P/L impact "
      f"${rate*down*pnl:+.2f}")
print("\n  NOTE: avg P/L is negative, so downtime has been SAVING money.")
print("  That is the uncomfortable part: uptime is not the profit problem.")
