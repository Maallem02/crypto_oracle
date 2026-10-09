"""What does raising the metals cap 7% -> 15% actually do?

Key mechanic: metals are ALREADY at the 0.01 lot floor. Raising the cap
therefore does NOT increase position size - it cannot, 0.01 is the minimum.
It only lets WIDER STOPS through instead of rejecting them.

So the question is twofold:
  1. How many metal signals is the 7% cap currently rejecting?
  2. For those, what stop width / dollar risk would 15% permit?

And the one that matters for survival: how many consecutive metal losses at
15% before the -35% SURVIVAL halt fires?
"""
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# The rejection message embeds the exact numbers:
#   "Risque trop eleve pour XAUUSD: lot 0.01 risque $12.18 = 8% du solde $150.00"
RE = re.compile(r"risque \$([0-9.]+) = ([0-9.]+)% du solde \$([0-9.]+)")

rows = list(db.execute(
    "select timestamp, symbol, reason from rejection_log "
    "where reason like '%Risque trop%' and timestamp >= '2026-08-01' "
    "order by timestamp"))
print(f"cap rejections logged since 08-01: {len(rows)}\n")

hits = []
for r in rows:
    m = RE.search(str(r["reason"]))
    if not m:
        continue
    hits.append(dict(ts=r["timestamp"], sym=(r["symbol"] or "").upper(),
                     risk=float(m.group(1)), pct=float(m.group(2)),
                     bal=float(m.group(3))))

print("=" * 88)
print("WHICH SYMBOLS IS THE 7% CAP BLOCKING?")
print("=" * 88)
bysym = defaultdict(list)
for h in hits:
    key = next((k for k in ("XAU", "XAG", "BTC", "ETH", "EUR") if k in h["sym"]),
               h["sym"])
    bysym[key].append(h)
for k in sorted(bysym, key=lambda s: -len(bysym[s])):
    v = bysym[k]
    pcts = sorted(x["pct"] for x in v)
    print(f"  {k:6s} {len(v):5d} rejections   "
          f"required risk%: min {pcts[0]:5.1f}  med {pcts[len(pcts)//2]:5.1f}  "
          f"max {pcts[-1]:5.1f}")

print("\n" + "=" * 88)
print("HOW MANY WOULD A 15% CAP LET THROUGH?")
print("=" * 88)
for k in ("XAU", "XAG"):
    v = bysym.get(k, [])
    if not v:
        print(f"  {k}: no cap rejections logged")
        continue
    for cap in (7, 10, 12, 15, 20):
        passed = [x for x in v if x["pct"] <= cap]
        print(f"  {k}  cap {cap:2d}%  ->  {len(passed):5d}/{len(v)} "
              f"({100*len(passed)/len(v):3.0f}%) of blocked signals now pass")
    print()

print("=" * 88)
print("DISTRIBUTION OF REQUIRED RISK % (metals only)")
print("=" * 88)
met = [x for x in hits if any(k in x["sym"] for k in ("XAU", "XAG"))]
buckets = [(7, 9), (9, 11), (11, 13), (13, 15), (15, 20), (20, 999)]
for lo, hi in buckets:
    c = [x for x in met if lo <= x["pct"] < hi]
    print(f"  {lo:3d}-{hi:<3d}%  {len(c):5d}  {'#' * min(50, len(c)//3)}")

print("\n" + "=" * 88)
print("SURVIVAL MATH - consecutive metal losses before the -35% halt")
print("=" * 88)
BAL = 141.64
for cap in (0.07, 0.10, 0.15):
    bal = BAL
    n = 0
    seq = []
    while n < 12:
        loss = bal * cap
        bal -= loss
        n += 1
        dd = 100 * (BAL - bal) / BAL
        seq.append((n, bal, dd))
        if dd >= 35:
            break
    n35, bal35, dd35 = seq[-1]
    print(f"  cap {100*cap:4.0f}%  ->  {n35} consecutive losses reaches "
          f"{dd35:.1f}% drawdown (balance ${bal35:.2f})")

print("\n  probability of that losing streak, at the measured metal win rates:")
for name, wr in (("XAU  WR 40.4%", 0.404), ("XAG  WR 26.2%", 0.262),
                 ("metals WR 33.7%", 0.337)):
    for cap, nloss in ((0.07, 6), (0.15, 3)):
        p = (1 - wr) ** nloss
        print(f"    {name:18s} cap {100*cap:3.0f}%  "
              f"{nloss} straight losses: {100*p:5.1f}% chance per sequence")
