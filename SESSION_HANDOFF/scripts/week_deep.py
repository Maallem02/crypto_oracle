"""Two follow-ups the week summary raises:

 1. GOLD is the week's worst symbol (PF 0.49, -$27.84 over 10 trades) and the
    15% cap has not helped it. Is the damage concentrated in a few trades, or
    is gold broadly broken right now?

 2. PENDING orders went PF 0.32 -> 0.89 after the expiry change. That is the
    biggest single improvement in the data - but n=7. Check the MECHANISM
    (resting time before fill), which is less noisy than the P/L.
"""
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

import MetaTrader5 as mt5

WEEK = datetime(2026, 9, 14)
CUT = datetime(2026, 9, 15, 22, 15)

mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 8, 25),
                              datetime.now() + timedelta(hours=6)) or []
orders = mt5.history_orders_get(datetime(2026, 8, 25),
                                datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)
o2p = {x.order: x.position_id for x in deals
       if x.entry == 0 and x.order and x.position_id}
mt5.shutdown()

pnl = {}
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if ins and outs:
        pnl[pid] = dict(
            t0=datetime.fromtimestamp(ins[0].time), sym=ins[0].symbol,
            dir="BUY" if ins[0].type == 0 else "SELL",
            net=sum(x.profit + x.commission + x.swap for x in ds),
            hold=(outs[-1].time - ins[0].time) / 60.0)

print("=" * 96)
print("1. GOLD THIS WEEK - every trade")
print("=" * 96)
gold = sorted([v for v in pnl.values()
               if "XAU" in v["sym"].upper() and v["t0"] >= WEEK],
              key=lambda z: z["t0"])
run = 0.0
for g in gold:
    run += g["net"]
    tag = "  (after cap change)" if g["t0"] >= CUT else ""
    print(f"  {g['t0']:%a %m-%d %H:%M}  {g['dir']:4s} {g['net']:+8.2f}  "
          f"held {g['hold']:5.0f}m   run {run:+8.2f}{tag}")
gl = [g["net"] for g in gold if g["net"] <= 0]
gw = [g["net"] for g in gold if g["net"] > 0]
print(f"\n  {len(gw)} wins / {len(gl)} losses")
print(f"  biggest loss ${min(gl) if gl else 0:.2f}   biggest win ${max(gw) if gw else 0:.2f}")
print(f"  losses concentrated? top-2 losses = "
      f"${sum(sorted(gl)[:2]):.2f} of ${sum(gl):.2f} total loss")
print(f"  by direction:  "
      f"BUY {sum(1 for g in gold if g['dir']=='BUY')} trades "
      f"${sum(g['net'] for g in gold if g['dir']=='BUY'):+.2f}   |   "
      f"SELL {sum(1 for g in gold if g['dir']=='SELL')} trades "
      f"${sum(g['net'] for g in gold if g['dir']=='SELL'):+.2f}")

print("\n  gold over the whole test, week by week:")
allgold = sorted([v for v in pnl.values() if "XAU" in v["sym"].upper()],
                 key=lambda z: z["t0"])
bw = defaultdict(list)
for g in allgold:
    bw[g["t0"].strftime("%Y-W%W")].append(g)
for k in sorted(bw):
    v = bw[k]
    w = [x for x in v if x["net"] > 0]
    gp = sum(x["net"] for x in w)
    gll = abs(sum(x["net"] for x in v if x["net"] <= 0))
    print(f"    {k}  n={len(v):3d}  ${sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(w)/len(v):5.1f}%  PF={(gp/gll if gll else 9.99):5.2f}")

print("\n" + "=" * 96)
print("2. PENDING ORDERS - the MECHANISM, not the P/L")
print("=" * 96)
PEND = {2: "buy_limit", 3: "sell_limit", 4: "buy_stop", 5: "sell_stop"}
rows = []
for o in orders:
    if o.type not in PEND or o.state != 4:
        continue
    pid = o2p.get(o.ticket)
    if not pid or pid not in pnl:
        continue
    rows.append(dict(setup=datetime.fromtimestamp(o.time_setup),
                     rest=(o.time_done - o.time_setup) / 60.0,
                     net=pnl[pid]["net"], sym=o.symbol))
rows.sort(key=lambda z: z["setup"])

for lbl, sub in (("BEFORE the 60-min expiry", [r for r in rows if r["setup"] < CUT]),
                 ("AFTER the 60-min expiry", [r for r in rows if r["setup"] >= CUT])):
    if not sub:
        continue
    over = [r for r in sub if r["rest"] > 60]
    w = [r for r in sub if r["net"] > 0]
    print(f"\n  {lbl}:  n={len(sub)}")
    print(f"    median rest before filling : {median(r['rest'] for r in sub):6.1f} min")
    print(f"    filled after resting >60min: {len(over)}/{len(sub)} "
          f"({100*len(over)/len(sub):.0f}%)   their P/L ${sum(r['net'] for r in over):+.2f}")
    print(f"    net ${sum(r['net'] for r in sub):+8.2f}   WR {100*len(w)/len(sub):.1f}%")

late = [r for r in rows if r["setup"] >= CUT and r["rest"] > 60]
if late:
    print("\n  limits that STILL filled after 60+ min (should have been cancelled):")
    for r in late:
        print(f"    {r['setup']:%a %m-%d %H:%M} {r['sym']:9s} rested {r['rest']:6.1f} min "
              f"-> ${r['net']:+.2f}")
    print("    (the scan loop does the cancelling - a stalled/dead bot cannot)")
