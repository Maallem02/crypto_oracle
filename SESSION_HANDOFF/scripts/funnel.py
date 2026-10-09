"""Why so few trades? Separate DOWNTIME from GATING.

The backend died Mon 14/09 12:14. But it was alive from Mon 03:00. So:
  1. How much of the tradeable week was the bot actually running?
  2. While running, how far did signals get through the gate stack?

A gate that rejects 99% of what reaches it is the real answer, not the crash.
"""
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

MON = "2026-09-14T00:00:00"

# ── 1. uptime: gaps between consecutive scans ───────────────────────────────
ts = [r[0] for r in db.execute(
    "select distinct timestamp from rejection_log where timestamp >= ? "
    "order by timestamp", (MON,))]
print("=" * 88)
print(f"SCAN COVERAGE SINCE MONDAY 00:00   ({len(ts)} distinct scan timestamps)")
print("=" * 88)
if ts:
    print(f"  first scan  {ts[0]}")
    print(f"  last  scan  {ts[-1]}")
    T = [datetime.fromisoformat(x) for x in ts]
    gaps = [(T[i] - T[i - 1], T[i - 1], T[i]) for i in range(1, len(T))]
    big = [g for g in gaps if g[0] > timedelta(minutes=20)]
    print(f"\n  gaps over 20 min: {len(big)}")
    for d, a, b in big[:10]:
        print(f"    {a:%m-%d %H:%M} -> {b:%m-%d %H:%M}   DOWN {d.total_seconds()/3600:.1f} h")
    now = datetime.now()
    print(f"    {T[-1]:%m-%d %H:%M} -> now ({now:%m-%d %H:%M})   "
          f"DOWN {(now-T[-1]).total_seconds()/3600:.1f} h   <- current outage")

# ── 2. the gate funnel ──────────────────────────────────────────────────────
rows = list(db.execute(
    "select symbol, reason from rejection_log where timestamp >= ?", (MON,)))
print("\n" + "=" * 88)
print(f"GATE FUNNEL WHILE ALIVE   ({len(rows)} rejection rows)")
print("=" * 88)
c = Counter(str(r["reason"]).split(":")[0].strip()[:56] for r in rows)
tot = sum(c.values())
for k, v in c.most_common(20):
    print(f"  {v:7d}  {100*v/tot:5.1f}%  {k}")

# ── 3. what did the SCORE=0 rejections actually say? ────────────────────────
print("\n" + "=" * 88)
print("THE REAL BLOCKER: why does score stay at 0/60?")
print("=" * 88)
det = Counter()
for r in db.execute("select reason, data from rejection_log where timestamp >= ? "
                    "and reason like 'score=0%'", (MON,)):
    d = str(r["data"] or "")
    for tag in ("No Liquidity Grab", "COUNTER-TREND REJECTED", "P/D REJECTED",
                "HTF", "CHOCH", "CISD", "no_confirmation"):
        if tag in d:
            det[tag] += 1
            break
    else:
        det["(no detail logged)"] += 1
for k, v in det.most_common():
    print(f"  {v:7d}  {k}")

# ── 4. trades actually taken ────────────────────────────────────────────────
print("\n" + "=" * 88)
print("SIGNALS THAT PASSED (scalp_log) SINCE MONDAY")
print("=" * 88)
sg = list(db.execute("select timestamp,symbol,ticket,signal_type,bias "
                     "from scalp_log where timestamp >= ? order by timestamp", (MON,)))
print(f"  {len(sg)} signal rows")
for r in sg:
    print(f"    {r['timestamp'][:16]}  {str(r['symbol']):8s} {str(r['bias']):5s} "
          f"{str(r['signal_type'] or ''):14s} ticket={r['ticket']}")

# ── 5. week-by-week trade counts for context ────────────────────────────────
print("\n" + "=" * 88)
print("SIGNAL VOLUME BY DAY (last 14 days) - is the drought new?")
print("=" * 88)
byday = defaultdict(int)
for r in db.execute("select timestamp from scalp_log where timestamp >= '2026-09-01'"):
    byday[r[0][:10]] += 1
for k in sorted(byday):
    print(f"    {k}  {byday[k]:3d}  {'#'*byday[k]}")
