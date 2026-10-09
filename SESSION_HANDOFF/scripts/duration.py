"""Trades under 1h lose $0.81 each; trades held 1-4h MAKE $0.83. Why?

CAUTION: duration is an OUTCOME, not a setting. A loser that hits its stop ends
the trade - so "short trades lose" is partly mechanical and NOT directly
actionable. The question is whether the short bucket is:

  (a) purely stop-outs  -> mechanical, nothing to fix here
  (b) a normal win rate with TINY wins -> the exit is cutting winners early,
      which IS fixable

Decompose it. Then check what the fast LOSERS looked like: did they ever go
positive before dying?
"""
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 6, 1),
                              datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

T = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    T.append(dict(
        t0=datetime.fromtimestamp(ins[0].time),
        sym=ins[0].symbol.replace("USDm", "").replace("m", ""),
        net=sum(x.profit + x.commission + x.swap for x in ds),
        hold=(outs[-1].time - ins[0].time) / 60.0))      # minutes
mt5.shutdown()

BUCKETS = [(0, 5, "under 5 min"), (5, 15, "5-15 min"), (15, 30, "15-30 min"),
           (30, 60, "30-60 min"), (60, 120, "1-2 h"), (120, 240, "2-4 h"),
           (240, 720, "4-12 h"), (720, 1e9, "over 12 h")]

print("=" * 100)
print("DECOMPOSING THE HOLDING-TIME EFFECT")
print("=" * 100)
print(f"  {'bucket':13s} {'n':>4s} {'net':>9s} {'avg':>7s} {'WR':>7s} "
      f"{'avgWin':>8s} {'avgLoss':>8s} {'PF':>5s}")
for lo, hi, lbl in BUCKETS:
    v = [x for x in T if lo <= x["hold"] < hi]
    if not v:
        continue
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    print(f"  {lbl:13s} {len(v):4d} {sum(x['net'] for x in v):+9.2f} "
          f"{mean(x['net'] for x in v):+7.2f} {100*len(w)/len(v):6.1f}% "
          f"{mean(x['net'] for x in w) if w else 0:+8.2f} "
          f"{mean(x['net'] for x in l) if l else 0:+8.2f} "
          f"{(gp/gl if gl else 9.99):5.2f}")

print("\n" + "=" * 100)
print("HOW LONG DO WINNERS vs LOSERS LIVE?  (the mechanical check)")
print("=" * 100)
w = [x for x in T if x["net"] > 0]
l = [x for x in T if x["net"] <= 0]
print(f"  winners n={len(w):4d}  median hold {median(x['hold'] for x in w):7.1f} min  "
      f"mean {mean(x['hold'] for x in w):7.1f}")
print(f"  losers  n={len(l):4d}  median hold {median(x['hold'] for x in l):7.1f} min  "
      f"mean {mean(x['hold'] for x in l):7.1f}")
print("\n  share of each bucket that is a LOSS:")
for lo, hi, lbl in BUCKETS:
    v = [x for x in T if lo <= x["hold"] < hi]
    if not v:
        continue
    ll = [x for x in v if x["net"] <= 0]
    print(f"    {lbl:13s} {100*len(ll)/len(v):5.1f}% losses   "
          f"({len(ll)}/{len(v)})  {'#'*int(40*len(ll)/len(v))}")

print("\n" + "=" * 100)
print("THE SUB-1H BUCKET, SINCE 08-25 (current config) - is it still the problem?")
print("=" * 100)
rec = [x for x in T if x["t0"] >= datetime(2026, 8, 25)]
for lo, hi, lbl in [(0, 60, "under 1 h"), (60, 240, "1-4 h"), (240, 1e9, "over 4 h")]:
    v = [x for x in rec if lo <= x["hold"] < hi]
    if not v:
        continue
    ww = [x for x in v if x["net"] > 0]
    ll = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in ww), abs(sum(x["net"] for x in ll))
    print(f"  {lbl:12s} n={len(v):4d}  net={sum(x['net'] for x in v):+9.2f}  "
          f"WR={100*len(ww)/len(v):5.1f}%  "
          f"avgW={mean(x['net'] for x in ww) if ww else 0:+7.2f}  "
          f"avgL={mean(x['net'] for x in ll) if ll else 0:+7.2f}  "
          f"PF={(gp/gl if gl else 9.99):5.2f}")

print("\n  per symbol, sub-1h only, since 08-25:")
sub = [x for x in rec if x["hold"] < 60]
for sym in sorted({x["sym"] for x in sub}):
    v = [x for x in sub if x["sym"] == sym]
    ww = [x for x in v if x["net"] > 0]
    print(f"    {sym:6s} n={len(v):3d}  net={sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(ww)/len(v):5.1f}%")
