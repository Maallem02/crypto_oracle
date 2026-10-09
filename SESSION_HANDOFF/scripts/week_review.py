"""Full week review: Mon 2026-09-14 -> Fri 2026-09-18.

Three changes went in mid-week (all at 09-15 22:15):
    pending_max_age_minutes  480 -> 60
    XAU/XAG risk cap          7% -> 15%
    watchdog built (never registered, so it never ran)

Judge each on its own terms, and separate what the BOT did from what the
DOWNTIME did, because ~18 hours were lost.
"""
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median

import MetaTrader5 as mt5

WEEK = datetime(2026, 9, 14)
CUT = datetime(2026, 9, 15, 22, 15)          # when the changes went live
DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"

mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 7, 27),
                              datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
sig = {int(r["ticket"]): dict(r) for r in
       db.execute("select * from scalp_log where timestamp>='2026-07-25'")
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
    m = SL_RE.search(outs[-1].comment or "")
    sl = float(m.group(1)) if m else d0.get("sl")
    entry = ins[0].price
    risk = abs(entry - sl) if sl else None
    T.append(dict(
        t0=t0, key=key, sym=ins[0].symbol,
        dir="BUY" if ins[0].type == 0 else "SELL",
        net=sum(x.profit + x.commission + x.swap for x in ds),
        risk=risk, atr=atr,
        in_atr=(risk / atr) if (risk and atr) else None,
        mode="pending" if "-P" in (ins[0].comment or "") else "market",
        hold=(outs[-1].time - ins[0].time) / 60.0,
        after=t0 >= CUT))
acct = mt5.account_info()
mt5.shutdown()
T.sort(key=lambda x: x["t0"])
W = [x for x in T if x["t0"] >= WEEK]


def rep(v, label, pad=30):
    if not v:
        print(f"  {label:<{pad}} n=  0")
        return
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    aw = mean(x["net"] for x in w) if w else 0
    al = abs(mean(x["net"] for x in l)) if l else 1
    print(f"  {label:<{pad}} n={len(v):3d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(w)/len(v):5.1f}%  avgW=${aw:+6.2f}  avgL=${-al:+6.2f}  "
          f"RR={aw/al:4.2f}  PF={(gp/gl if gl else 9.99):5.2f}")


print("=" * 110)
print(f"WEEK REVIEW  Mon 09-14 -> Fri 09-18     {len(W)} trades")
print("=" * 110)
rep(W, "THE WEEK")
print()
print("  day by day:")
byday = defaultdict(list)
for x in W:
    byday[x["t0"].strftime("%a %m-%d")].append(x)
run = 0.0
for k in sorted(byday, key=lambda d: byday[d][0]["t0"]):
    v = byday[k]
    run += sum(x["net"] for x in v)
    rep(v, f"  {k}  (cum ${run:+7.2f})", 30)

print("\n" + "=" * 110)
print("THE CHANGES: before vs after 09-15 22:15")
print("=" * 110)
rep([x for x in T if datetime(2026, 8, 25) <= x["t0"] < CUT], "08-25 -> 09-15 (before)")
rep([x for x in W if x["after"]], "since the changes")
print()
for k in ("XAU", "XAG", "ETH", "BTC"):
    rep([x for x in W if x["after"] and x["key"] == k], f"  after: {k}")

print("\n  and the week's first half, for contrast:")
for k in ("XAU", "XAG", "ETH", "BTC"):
    rep([x for x in W if not x["after"] and x["key"] == k], f"  before: {k}")

print("\n" + "=" * 110)
print("PER SYMBOL, WHOLE WEEK")
print("=" * 110)
for k in sorted({x["key"] for x in W},
                key=lambda s: -sum(y["net"] for y in W if y["key"] == s)):
    rep([x for x in W if x["key"] == k], k, 12)

print("\n" + "=" * 110)
print("WEEK IN CONTEXT - every week of the test")
print("=" * 110)
wk = defaultdict(list)
for x in T:
    wk[x["t0"].strftime("%Y-W%W")].append(x)
for k in sorted(wk):
    rep(wk[k], k, 12)

print("\n" + "=" * 110)
print("DID THE CAP CHANGE DO WHAT IT WAS FOR?  metal stop width")
print("=" * 110)
for k in ("XAU", "XAG"):
    b = [x["in_atr"] for x in T if x["key"] == k and x["in_atr"]
         and datetime(2026, 8, 25) <= x["t0"] < CUT]
    a = [x["in_atr"] for x in W if x["key"] == k and x["in_atr"] and x["after"]]
    print(f"  {k}  before n={len(b):3d} med {median(b) if b else 0:.2f}xATR   ->   "
          f"after n={len(a):3d} med {median(a) if a else 0:.2f}xATR")
ml = [x for x in W if x["after"] and x["key"] in ("XAU", "XAG") and x["net"] < 0]
if ml:
    print(f"\n  worst metal loss since the cap change: ${min(x['net'] for x in ml):.2f}"
          f"   (15% of ~$135 would allow $20.25)")

print("\n" + "=" * 110)
print("PENDING EXPIRY (480 -> 60 min): did resting limits stop getting picked off?")
print("=" * 110)
for lbl, sub in (("before the change", [x for x in T if datetime(2026, 8, 25) <= x["t0"] < CUT]),
                 ("after the change", [x for x in W if x["after"]])):
    p = [x for x in sub if x["mode"] == "pending"]
    m = [x for x in sub if x["mode"] == "market"]
    print(f"\n  {lbl}:")
    rep(p, "    pending", 14)
    rep(m, "    market", 14)

print("\n" + "=" * 110)
print("UPTIME THIS WEEK  (session hours only - dead zone & weekend excluded)")
print("=" * 110)
ts = [datetime.fromisoformat(r[0]) for r in db.execute(
    "select distinct timestamp from rejection_log where timestamp >= ? order by timestamp",
    (WEEK.isoformat(),))]


def session_seconds(a, b):
    tot = 0.0
    cur = a.replace(minute=0, second=0, microsecond=0)
    while cur < b:
        nxt = cur + timedelta(hours=1)
        lo, hi = max(a, cur), min(b, nxt)
        if hi > lo:
            u = cur - timedelta(hours=1)       # local UTC+1 -> UTC
            if u.weekday() < 5 and not (u.hour >= 21 or u.hour < 2):
                tot += (hi - lo).total_seconds()
        cur = nxt
    return tot


END = min(datetime.now(), datetime(2026, 9, 18, 23, 59))
total = session_seconds(WEEK, END)
outages = [(ts[i-1], ts[i]) for i in range(1, len(ts))
           if ts[i] - ts[i-1] > timedelta(minutes=25)]
real = [(a, b, session_seconds(a, b) / 3600) for a, b in outages]
real = [x for x in real if x[2] >= 0.3]
down = sum(x[2] for x in real)
for a, b, h in real:
    print(f"    {a:%a %m-%d %H:%M} -> {b:%a %m-%d %H:%M}   {h:5.1f}h lost")
print(f"\n  session hours in the week: {total/3600:6.1f}")
print(f"  lost:                      {down:6.1f}")
print(f"  UPTIME:                    {100*(1-down/(total/3600)):6.1f}%")
rate = len(W) / (total/3600 - down) if (total/3600 - down) else 0
print(f"\n  trades taken: {len(W)} in {total/3600-down:.0f} live hours = {rate:.2f}/h")
print(f"  trades missed to downtime (est): {rate*down:.0f}")

print("\n" + "=" * 110)
print("ACCOUNT")
print("=" * 110)
print(f"  balance now ${acct.balance:.2f}   equity ${acct.equity:.2f}")
print(f"  week P/L    ${sum(x['net'] for x in W):+.2f}  over {len(W)} trades")
print(f"  a $61.79 deposit landed 09-14 03:40 (balance 88.21 -> 150.00)")
