"""Which symbols should stay? Lifetime, not a 3-week window.

XAG and XAU only have ~16 trades since 08-25 - far too few to cut on. Use the
full history, then check whether the recent window agrees with the lifetime
read. A symbol that is bad lifetime AND bad recently is a real cut. A symbol
that is bad lifetime but improving may just be responding to the fixes.
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
    T.append(dict(t0=datetime.fromtimestamp(ins[0].time),
                  sym=ins[0].symbol.replace("USDm", "").replace("m", ""),
                  pnl=sum(x.profit + x.commission + x.swap for x in ds)))
mt5.shutdown()
T.sort(key=lambda x: x["t0"])


def stats(v):
    if not v:
        return None
    w = [x for x in v if x["pnl"] > 0]
    l = [x for x in v if x["pnl"] <= 0]
    gp, gl = sum(x["pnl"] for x in w), abs(sum(x["pnl"] for x in l))
    return dict(n=len(v), net=sum(x["pnl"] for x in v),
                wr=100 * len(w) / len(v),
                aw=mean(x["pnl"] for x in w) if w else 0,
                al=mean(x["pnl"] for x in l) if l else 0,
                pf=gp / gl if gl else 9.99)


print("=" * 104)
print("LIFETIME vs RECENT, PER SYMBOL")
print("=" * 104)
print(f"  {'sym':6s} | {'LIFETIME':^44s} | {'SINCE 09-01':^40s}")
print(f"  {'':6s} | {'n':>4s} {'net':>9s} {'WR':>6s} {'avgW':>7s} {'avgL':>7s} {'PF':>5s} "
      f"| {'n':>4s} {'net':>9s} {'WR':>6s} {'PF':>5s}")
syms = sorted({x["sym"] for x in T}, key=lambda s: sum(
    y["pnl"] for y in T if y["sym"] == s))
for s in syms:
    a = stats([x for x in T if x["sym"] == s])
    b = stats([x for x in T if x["sym"] == s and x["t0"] >= datetime(2026, 9, 1)])
    line = (f"  {s:6s} | {a['n']:4d} {a['net']:+9.2f} {a['wr']:5.1f}% "
            f"{a['aw']:+7.2f} {a['al']:+7.2f} {a['pf']:5.2f} | ")
    line += (f"{b['n']:4d} {b['net']:+9.2f} {b['wr']:5.1f}% {b['pf']:5.2f}"
             if b else f"{'-':>4s} {'-':>9s} {'-':>6s} {'-':>5s}")
    print(line)

print("\n" + "=" * 104)
print("IF WE REMOVED A SYMBOL, WHAT WOULD THE REST HAVE DONE?  (since 08-25)")
print("=" * 104)
recent = [x for x in T if x["t0"] >= datetime(2026, 8, 25)]
base = stats(recent)
print(f"  {'keep all':22s} n={base['n']:4d}  net={base['net']:+9.2f}  "
      f"WR={base['wr']:5.1f}%  PF={base['pf']:5.2f}")
for s in syms:
    v = [x for x in recent if x["sym"] != s]
    st = stats(v)
    if not st:
        continue
    print(f"  {'drop ' + s:22s} n={st['n']:4d}  net={st['net']:+9.2f}  "
          f"WR={st['wr']:5.1f}%  PF={st['pf']:5.2f}   "
          f"delta={st['net']-base['net']:+8.2f}")

print("\n  and dropping the two worst together:")
for pair in (("BTC", "XAU"), ("BTC", "XAG"), ("XAU", "XAG"), ("BTC", "XAU", "XAG")):
    v = [x for x in recent if x["sym"] not in pair]
    st = stats(v)
    if st:
        print(f"  {'drop ' + '+'.join(pair):22s} n={st['n']:4d}  net={st['net']:+9.2f}  "
              f"WR={st['wr']:5.1f}%  PF={st['pf']:5.2f}   "
              f"delta={st['net']-base['net']:+8.2f}")

print("\n" + "=" * 104)
print("BTC MONTH BY MONTH - has it EVER worked?")
print("=" * 104)
bym = defaultdict(list)
for x in T:
    if x["sym"] == "BTC":
        bym[x["t0"].strftime("%Y-%m")].append(x)
for k in sorted(bym):
    st = stats(bym[k])
    print(f"  {k}  n={st['n']:3d}  net={st['net']:+8.2f}  WR={st['wr']:5.1f}%  "
          f"PF={st['pf']:5.2f}")
