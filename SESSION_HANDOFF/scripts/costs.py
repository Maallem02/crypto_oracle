"""How much of the loss is TRADING COSTS rather than being wrong?

Every symbol sits 1-4 win-rate points below its own breakeven. That is the
signature of a cost drag, not a broken edge. Split gross P/L from commission
and swap, and compute what the record looks like before costs.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

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
    gross = sum(x.profit for x in ds)
    comm = sum(x.commission for x in ds)
    swap = sum(x.swap for x in ds)
    T.append(dict(t0=datetime.fromtimestamp(ins[0].time),
                  sym=ins[0].symbol.replace("USDm", "").replace("m", ""),
                  gross=gross, comm=comm, swap=swap, net=gross + comm + swap,
                  hold=(outs[-1].time - ins[0].time) / 3600.0))
mt5.shutdown()

print("=" * 100)
print("COST DRAG - ALL TRADES SINCE 06-01")
print("=" * 100)
g = sum(x["gross"] for x in T)
c = sum(x["comm"] for x in T)
s = sum(x["swap"] for x in T)
n = sum(x["net"] for x in T)
print(f"  trades:            {len(T)}")
print(f"  gross P/L:      ${g:+10.2f}   <- pure price movement")
print(f"  commission:     ${c:+10.2f}")
print(f"  swap:           ${s:+10.2f}")
print(f"  NET:            ${n:+10.2f}")
print(f"\n  costs are {100*abs(c+s)/abs(n) if n else 0:.0f}% of the net loss")
print(f"  cost per trade:  ${(c+s)/len(T):+.3f}")


def blk(v, label):
    if not v:
        return
    gw = [x for x in v if x["gross"] > 0]
    nw = [x for x in v if x["net"] > 0]
    print(f"  {label:10s} n={len(v):4d}  "
          f"gross=${sum(x['gross'] for x in v):+8.2f} (WR {100*len(gw)/len(v):4.1f}%)  "
          f"net=${sum(x['net'] for x in v):+8.2f} (WR {100*len(nw)/len(v):4.1f}%)  "
          f"cost=${sum(x['comm']+x['swap'] for x in v):+7.2f}  "
          f"{100*len(gw)/len(v)-100*len(nw)/len(v):+4.1f} WR pts lost to cost")


print("\n" + "=" * 100)
print("PER SYMBOL - win rate BEFORE vs AFTER costs")
print("=" * 100)
for sym in sorted({x["sym"] for x in T},
                  key=lambda s2: -sum(y["net"] for y in T if y["sym"] == s2)):
    blk([x for x in T if x["sym"] == sym], sym)

print("\n" + "=" * 100)
print("THE ACTIVE FOUR, SINCE 08-25")
print("=" * 100)
rec = [x for x in T if x["t0"] >= datetime(2026, 8, 25)
       and x["sym"] in ("BTC", "ETH", "XAU", "XAG")]
blk(rec, "ALL 4")
for sym in ("BTC", "ETH", "XAU", "XAG"):
    blk([x for x in rec if x["sym"] == sym], sym)

print("\n" + "=" * 100)
print("SWAP vs HOLDING TIME - is holding longer being taxed?")
print("=" * 100)
for lo, hi, lbl in [(0, 1, "under 1h"), (1, 4, "1-4h"), (4, 12, "4-12h"),
                    (12, 24, "12-24h"), (24, 1e9, "over 24h")]:
    v = [x for x in T if lo <= x["hold"] < hi]
    if not v:
        continue
    print(f"  {lbl:10s} n={len(v):4d}  avg swap ${mean(x['swap'] for x in v):+6.3f}  "
          f"avg comm ${mean(x['comm'] for x in v):+6.3f}  "
          f"avg net ${mean(x['net'] for x in v):+6.2f}")
