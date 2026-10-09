"""The actual expectancy, from MT5 deals - not from scalp_log.

scalp_log only has rows for signals the bot logged. MT5 deals are the ground
truth for what the account did.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 7, 27),
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
    T.append(dict(t0=datetime.fromtimestamp(ins[0].time),
                  sym=ins[0].symbol.replace("USDm", "").replace("m", ""),
                  pnl=sum(x.profit + x.commission + x.swap for x in ds)))
mt5.shutdown()
T.sort(key=lambda x: x["t0"])


def rep(v, label):
    if not v:
        print(f"  {label:22s}  n=0")
        return
    w = [x for x in v if x["pnl"] > 0]
    l = [x for x in v if x["pnl"] <= 0]
    gp, gl = sum(x["pnl"] for x in w), abs(sum(x["pnl"] for x in l))
    print(f"  {label:22s} n={len(v):4d}  net=${sum(x['pnl'] for x in v):+9.2f}  "
          f"avg=${mean(x['pnl'] for x in v):+6.2f}  WR={100*len(w)/len(v):5.1f}%  "
          f"avgW=${mean(x['pnl'] for x in w) if w else 0:+6.2f}  "
          f"avgL=${mean(x['pnl'] for x in l) if l else 0:+6.2f}  "
          f"PF={(gp/gl if gl else 9.99):5.2f}")


print("=" * 108)
print("GROUND TRUTH - ALL BOT TRADES")
print("=" * 108)
rep(T, "since 07-27 (all)")
rep([x for x in T if x["t0"] >= datetime(2026, 8, 25)], "since 08-25")
rep([x for x in T if x["t0"] >= datetime(2026, 9, 7)], "since 09-07")
rep([x for x in T if x["t0"] >= datetime(2026, 9, 14)], "this week")

print("\n  by week:")
wk = defaultdict(list)
for x in T:
    wk[x["t0"].strftime("%Y-W%W")].append(x)
for k in sorted(wk):
    rep(wk[k], k)

print("\n  by symbol (since 08-25):")
recent = [x for x in T if x["t0"] >= datetime(2026, 8, 25)]
bys = defaultdict(list)
for x in recent:
    bys[x["sym"]].append(x)
for k in sorted(bys, key=lambda s: -sum(y["pnl"] for y in bys[s])):
    rep(bys[k], k)

print("\n" + "=" * 108)
print("WHAT WOULD IT TAKE TO BREAK EVEN?")
print("=" * 108)
w = [x for x in recent if x["pnl"] > 0]
l = [x for x in recent if x["pnl"] <= 0]
wr = len(w) / len(recent)
aw, al = mean(x["pnl"] for x in w), abs(mean(x["pnl"] for x in l))
print(f"  now:  WR {100*wr:.1f}%   avgWin ${aw:.2f}   avgLoss ${al:.2f}   "
      f"RR {aw/al:.2f}   exp ${wr*aw-(1-wr)*al:+.2f}/trade")
print(f"\n  to break even at WR {100*wr:.1f}%, avgWin must be ${al*(1-wr)/wr:.2f} "
      f"(RR {al*(1-wr)/wr/al:.2f})  -> need {100*(al*(1-wr)/wr/aw - 1):+.0f}% bigger wins")
print(f"  to break even at RR {aw/al:.2f}, WR must be "
      f"{100/(1+aw/al):.1f}%  -> need {100/(1+aw/al)-100*wr:+.1f} points more")
