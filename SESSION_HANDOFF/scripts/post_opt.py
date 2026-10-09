"""How did the 09-15 22:15 changes actually do?

Three changes went live together:
  1. pending_max_age_minutes 480 -> 60
  2. XAU/XAG risk cap 7% -> 15%
  3. (watchdog, which does not affect trading)

Questions, in order of what would falsify them:
  a. Did metal STOPS actually get wider? That is the whole point of the cap
     change - metals are at the 0.01 lot floor so size cannot increase.
  b. Did any metal loss cost ~$18.83 (15%) rather than ~$8.80 (7%)?
  c. Three consecutive metal losses (the halt scenario)?
  d. Did pendings stop filling after 60 minutes of resting?
  e. Was there downtime the watchdog should have caught?
"""
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

import MetaTrader5 as mt5

CUT = datetime(2026, 9, 15, 22, 15)
DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"

mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 8, 25),
                              datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
sig = {int(r["ticket"]): dict(r) for r in
       db.execute("select * from scalp_log where timestamp>='2026-08-20'")
       if r["ticket"]}

CONTRACT = {"XAU": 100.0, "XAG": 5000.0, "BTC": 1.0, "ETH": 1.0}
SL_RE = re.compile(r"\[sl\s+([0-9.]+)\]")

T = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    sym = ins[0].symbol.upper()
    key = next((k for k in CONTRACT if k in sym), None)
    if not key:
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    atr = d0.get("atr")
    # real stop = broker's own exit comment when present, else the signal SL
    m = SL_RE.search(outs[-1].comment or "")
    sl = float(m.group(1)) if m else d0.get("sl")
    entry = ins[0].price
    risk = abs(entry - sl) if sl else None
    T.append(dict(
        t0=t0, key=key, sym=ins[0].symbol,
        dir="BUY" if ins[0].type == 0 else "SELL",
        net=sum(x.profit + x.commission + x.swap for x in ds),
        vol=ins[0].volume, risk=risk, atr=atr,
        in_atr=(risk / atr) if (risk and atr) else None,
        dollar_risk=(risk * ins[0].volume * CONTRACT[key]) if risk else None,
        pct_bal=None,
        mode="pending" if "-P" in (ins[0].comment or "") else "market",
        hold=(outs[-1].time - ins[0].time) / 60.0,
        after=t0 >= CUT))
mt5.shutdown()
T.sort(key=lambda x: x["t0"])

new = [x for x in T if x["after"]]
old = [x for x in T if not x["after"]]
print("=" * 104)
print(f"TRADES SINCE THE 09-15 22:15 CHANGES: {len(new)}")
print("=" * 104)
print(f"  {'when':14s} {'sym':9s} {'dir':4s} {'mode':8s} {'$':>8s} "
      f"{'stop':>9s} {'xATR':>6s} {'$ risk':>8s} {'hold':>7s}")
run = 0.0
for x in new:
    run += x["net"]
    print(f"  {x['t0']:%m-%d %H:%M}  {x['sym']:9s} {x['dir']:4s} {x['mode']:8s} "
          f"{x['net']:+8.2f} {x['risk'] if x['risk'] else 0:9.4f} "
          f"{x['in_atr'] if x['in_atr'] else 0:6.2f} "
          f"{x['dollar_risk'] if x['dollar_risk'] else 0:8.2f} "
          f"{x['hold']:6.0f}m   run {run:+7.2f}")


def rep(v, label):
    if not v:
        print(f"  {label:30s} n=  0")
        return
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    print(f"  {label:30s} n={len(v):3d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(w)/len(v):5.1f}%  "
          f"avgW=${mean(x['net'] for x in w) if w else 0:+6.2f}  "
          f"avgL=${mean(x['net'] for x in l) if l else 0:+6.2f}  "
          f"PF={(gp/gl if gl else 9.99):5.2f}")


print("\n" + "=" * 104)
print("BEFORE vs AFTER")
print("=" * 104)
rep(old, "08-25 -> 09-15 (before)")
rep(new, "since 09-15 22:15 (after)")
print()
for k in ("XAU", "XAG", "ETH", "BTC"):
    rep([x for x in new if x["key"] == k], f"  after: {k}")

print("\n" + "=" * 104)
print("(a) DID METAL STOPS ACTUALLY GET WIDER?  stop / ATR")
print("=" * 104)
for k in ("XAU", "XAG"):
    b = [x["in_atr"] for x in old if x["key"] == k and x["in_atr"]]
    a = [x["in_atr"] for x in new if x["key"] == k and x["in_atr"]]
    bd = [x["dollar_risk"] for x in old if x["key"] == k and x["dollar_risk"]]
    ad = [x["dollar_risk"] for x in new if x["key"] == k and x["dollar_risk"]]
    print(f"  {k}  before: n={len(b):3d} med {median(b) if b else 0:.2f}xATR  "
          f"${median(bd) if bd else 0:5.2f} risk   ->   "
          f"after: n={len(a):3d} med {median(a) if a else 0:.2f}xATR  "
          f"${median(ad) if ad else 0:5.2f} risk")

print("\n" + "=" * 104)
print("(b) BIGGEST METAL LOSS SINCE THE CHANGE  (15% of ~$130 = ~$19.50)")
print("=" * 104)
ml = [x for x in new if x["key"] in ("XAU", "XAG") and x["net"] < 0]
if ml:
    for x in sorted(ml, key=lambda z: z["net"])[:6]:
        print(f"    {x['t0']:%m-%d %H:%M} {x['sym']:9s} ${x['net']:+7.2f}  "
              f"stop {x['risk']:.4f} ({x['in_atr'] if x['in_atr'] else 0:.2f}xATR)")
else:
    print("    no losing metal trade yet since the change")

print("\n" + "=" * 104)
print("(c) CONSECUTIVE METAL LOSSES (the halt scenario)")
print("=" * 104)
met = [x for x in new if x["key"] in ("XAU", "XAG")]
streak = worst = 0
for x in met:
    streak = streak + 1 if x["net"] <= 0 else 0
    worst = max(worst, streak)
print(f"    metal trades since change: {len(met)}   longest losing streak: {worst}")
print(f"    (3 straight would trip the -35% halt)")

print("\n" + "=" * 104)
print("(d) PENDING EXPIRY - are limits still filling after 60 min of resting?")
print("=" * 104)
mt5.initialize(timeout=10000)
orders = mt5.history_orders_get(CUT - timedelta(days=1),
                                datetime.now() + timedelta(hours=6)) or []
d2 = mt5.history_deals_get(CUT - timedelta(days=1),
                           datetime.now() + timedelta(hours=6)) or []
mt5.shutdown()
o2p = {x.order: x.position_id for x in d2 if x.entry == 0 and x.order and x.position_id}
pend = [o for o in orders if o.type in (2, 3, 4, 5) and o.state == 4
        and datetime.fromtimestamp(o.time_setup) >= CUT]
if pend:
    for o in sorted(pend, key=lambda z: z.time_setup):
        rest = (o.time_done - o.time_setup) / 60.0
        flag = "  <<< OVER 60 MIN" if rest > 60 else ""
        print(f"    {datetime.fromtimestamp(o.time_setup):%m-%d %H:%M} {o.symbol:9s} "
              f"rested {rest:6.1f} min before filling{flag}")
else:
    print("    no pending orders filled since the change")

print("\n" + "=" * 104)
print("(e) DOWNTIME SINCE THE CHANGE - did the watchdog need to fire?")
print("=" * 104)
ts = [datetime.fromisoformat(r[0]) for r in db.execute(
    "select distinct timestamp from rejection_log where timestamp >= ? order by timestamp",
    (CUT.isoformat(),))]
if ts:
    print(f"    scans from {ts[0]:%m-%d %H:%M} to {ts[-1]:%m-%d %H:%M}")
    gaps = [(ts[i-1], ts[i], (ts[i]-ts[i-1]).total_seconds()/3600)
            for i in range(1, len(ts)) if ts[i]-ts[i-1] > timedelta(minutes=25)]
    for a, b, h in gaps:
        # was it the dead zone (21:00-02:00 UTC = 22:00-03:00 local) or weekend?
        u = a - timedelta(hours=1)
        tag = "dead zone/weekend" if (u.hour >= 21 or u.hour < 2 or u.weekday() >= 5) \
              else "*** REAL OUTAGE ***"
        print(f"    {a:%a %m-%d %H:%M} -> {b:%a %m-%d %H:%M}  {h:5.1f}h  {tag}")
    if not gaps:
        print("    no gaps over 25 min")
