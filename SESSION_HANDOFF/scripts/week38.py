"""Week of Mon 2026-09-21 review.

Balance jumped 128.14 (Sun) -> 193.74 (Wed). Before crediting the bot, check
for a deposit: every balance operation (deal type 2) is listed separately.

Also: the 09-20 backend's log ends Mon 21/09 13:32, and the current process
started Tue 22/09 12:50 - that looks like crash #8 with ~a day lost. Measure it.

First gold-free week: gold was blocked 09-19. Judge silver alone in the
metals slot, and whether the portfolio is better without gold.
"""
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

import MetaTrader5 as mt5

WEEK = datetime(2026, 9, 21)
DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"

mt5.initialize(timeout=10000)
acct = mt5.account_info()
all_deals = mt5.history_deals_get(datetime(2026, 7, 27),
                                  datetime.now() + timedelta(hours=6)) or []
mt5.shutdown()

print("=" * 108)
print("1. WHERE DID THE BALANCE COME FROM?")
print("=" * 108)
since_sun = [d for d in all_deals if datetime.fromtimestamp(d.time) >= datetime(2026, 9, 20)]
funding = [d for d in since_sun if d.type == 2]
trading = [d for d in since_sun if d.type in (0, 1)]
fund_amt = sum(d.profit for d in funding)
trade_amt = sum(d.profit + d.commission + d.swap for d in trading)
for d in funding:
    print(f"  DEPOSIT/WITHDRAWAL  {datetime.fromtimestamp(d.time):%a %m-%d %H:%M}  "
          f"${d.profit:+.2f}   {d.comment}")
if not funding:
    print("  no deposits or withdrawals since Sunday")
print(f"\n  balance Sunday evening   $128.14")
print(f"  + deposits              ${fund_amt:+8.2f}")
print(f"  + trading P/L           ${trade_amt:+8.2f}")
print(f"  = balance now           ${128.14 + fund_amt + trade_amt:8.2f}   "
      f"(MT5 says ${acct.balance:.2f})")

pos = defaultdict(list)
for d in all_deals:
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
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    atr = d0.get("atr")
    m = SL_RE.search(outs[-1].comment or "")
    sl = float(m.group(1)) if m else d0.get("sl")
    entry = ins[0].price
    risk = abs(entry - sl) if sl else None
    exit_kind = ("tp" if "[tp" in (outs[-1].comment or "").lower() else
                 "sl" if m else "other")
    T.append(dict(
        t0=datetime.fromtimestamp(ins[0].time),
        t1=datetime.fromtimestamp(outs[-1].time),
        key=key, sym=ins[0].symbol,
        dir="BUY" if ins[0].type == 0 else "SELL",
        net=sum(x.profit + x.commission + x.swap for x in ds),
        risk=risk, atr=atr, in_atr=(risk / atr) if (risk and atr) else None,
        mode="pending" if "-P" in (ins[0].comment or "") else "market",
        hold=(outs[-1].time - ins[0].time) / 60.0,
        exit=exit_kind, comment=outs[-1].comment or ""))
T.sort(key=lambda x: x["t0"])


def rep(v, label, pad=28):
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


print("\n" + "=" * 108)
print("2. EVERY TRADE CLOSED SINCE MONDAY 09-21 (plus the carried-over gold)")
print("=" * 108)
W = [x for x in T if x["t1"] >= WEEK]
run = 0.0
print(f"  {'opened':14s} {'closed':14s} {'sym':9s} {'dir':4s} {'mode':8s} "
      f"{'$':>8s} {'xATR':>5s} {'hold':>7s}  exit")
for x in W:
    run += x["net"]
    print(f"  {x['t0']:%a %m-%d %H:%M} {x['t1']:%a %m-%d %H:%M} {x['sym']:9s} "
          f"{x['dir']:4s} {x['mode']:8s} {x['net']:+8.2f} "
          f"{x['in_atr'] if x['in_atr'] else 0:5.2f} {x['hold']:6.0f}m  "
          f"{x['exit']:5s} run {run:+8.2f}")

print("\n" + "=" * 108)
print("3. THE WEEK SO FAR")
print("=" * 108)
rep(W, "ALL")
for k in ("BTC", "ETH", "XAG", "XAU"):
    rep([x for x in W if x["key"] == k], f"  {k}")
print()
byday = defaultdict(list)
for x in W:
    byday[x["t1"].strftime("%a %m-%d")].append(x)
for k in sorted(byday, key=lambda d: byday[d][0]["t1"]):
    rep(byday[k], f"  {k}")

print("\n" + "=" * 108)
print("4. CONTEXT - every week of the test")
print("=" * 108)
wk = defaultdict(list)
for x in T:
    wk[x["t1"].strftime("%Y-W%W")].append(x)
for k in sorted(wk)[-6:]:
    rep(wk[k], k, 12)

print("\n" + "=" * 108)
print("5. SINCE THE 09-15 CHANGES (cap 15%, pending expiry 60 min)")
print("=" * 108)
CUT = datetime(2026, 9, 15, 22, 15)
post = [x for x in T if x["t0"] >= CUT]
rep([x for x in T if datetime(2026, 8, 25) <= x["t0"] < CUT], "before (08-25 -> 09-15)")
rep(post, "since the changes")
for k in ("BTC", "ETH", "XAG", "XAU"):
    rep([x for x in post if x["key"] == k], f"  {k}")
print()
rep([x for x in post if x["mode"] == "pending"], "  pending since")
rep([x for x in post if x["mode"] == "market"], "  market since")

print("\n" + "=" * 108)
print("6. SILVER - alone in the metals slot now")
print("=" * 108)
sv = [x for x in T if x["key"] == "XAG" and x["t0"] >= CUT]
for x in sv:
    print(f"  {x['t0']:%a %m-%d %H:%M} {x['dir']:4s} {x['mode']:8s} {x['net']:+8.2f}  "
          f"{x['in_atr'] if x['in_atr'] else 0:4.2f}xATR  held {x['hold']:5.0f}m")
streak = worst = 0
for x in sv:
    streak = streak + 1 if x["net"] <= 0 else 0
    worst = max(worst, streak)
print(f"\n  longest silver losing streak since the change: {worst}")

print("\n" + "=" * 108)
print("7. UPTIME THIS WEEK")
print("=" * 108)
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
            u = cur - timedelta(hours=1)
            if u.weekday() < 5 and not (u.hour >= 21 or u.hour < 2):
                tot += (hi - lo).total_seconds()
        cur = nxt
    return tot


START = datetime(2026, 9, 21, 3, 0)
total = session_seconds(START, datetime.now())
out = [(ts[i-1], ts[i]) for i in range(1, len(ts)) if ts[i] - ts[i-1] > timedelta(minutes=25)]
if ts and ts[0] - START > timedelta(minutes=25):
    out.insert(0, (START, ts[0]))
real = [(a, b, session_seconds(a, b) / 3600) for a, b in out]
real = [x for x in real if x[2] >= 0.3]
down = sum(x[2] for x in real)
for a, b, h in real:
    print(f"  {a:%a %m-%d %H:%M} -> {b:%a %m-%d %H:%M}   {h:5.1f}h lost")
print(f"\n  session hours so far: {total/3600:5.1f}   lost: {down:5.1f}   "
      f"UPTIME {100*(1-down/(total/3600)) if total else 0:5.1f}%")
