"""XAU avgR went +0.08 (before 09-01) -> -2.28 (since). That is NOT a worse
win rate - it means individual LOSSES are exceeding 1R, which they should not.

A stop at -1R that closes at -2R or -3R means one of:
  - the stop was never placed / was moved wrong
  - price gapped through it (news, session open)
  - slippage on the fill

List every metal trade since 09-01 with its exact R, its stop, and where it
actually closed.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
START = datetime(2026, 8, 20)
deals = mt5.history_deals_get(START, datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

db = sqlite3.connect(
    r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
sig = {int(r["ticket"]): dict(r) for r in
       db.execute("select * from scalp_log where timestamp>='2026-08-18'")
       if r["ticket"]}

rows = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    if not any(k in ins[0].symbol.upper() for k in ("XAU", "XAG")):
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    sl0 = d0.get("sl")
    entry = ins[0].price
    sign = 1 if ins[0].type == 0 else -1
    if not sl0:
        continue
    risk = abs(entry - sl0)
    if risk <= 0:
        continue
    exit_px = outs[-1].price
    r = sign * (exit_px - entry) / risk
    # where SHOULD it have closed if the stop held?
    overshoot = 0.0
    if r < -1.001:
        overshoot = abs(r) - 1.0
    rows.append(dict(
        t0=t0, sym=ins[0].symbol, dir="BUY" if sign > 0 else "SELL",
        entry=entry, sl=sl0, exit=exit_px, risk=risk, r=r,
        net=sum(x.profit + x.commission + x.swap for x in ds),
        over=overshoot, vol=ins[0].volume,
        hold=(outs[-1].time - ins[0].time) / 60.0,
        reason=getattr(outs[-1], "reason", None),
        comment=(outs[-1].comment or "")))
mt5.shutdown()
rows.sort(key=lambda x: x["t0"])

print("=" * 116)
print("EVERY METAL TRADE SINCE 08-20")
print("=" * 116)
print(f"  {'when':15s} {'sym':9s} {'dir':4s} {'entry':>10s} {'stop':>10s} "
      f"{'exit':>10s} {'risk':>7s} {'R':>7s} {'$':>8s} {'hold':>7s} {'exit comment':16s}")
for x in rows:
    flag = "  <<< BLEW THROUGH STOP" if x["over"] > 0.15 else ""
    print(f"  {x['t0']:%m-%d %H:%M}   {x['sym']:9s} {x['dir']:4s} "
          f"{x['entry']:10.3f} {x['sl']:10.3f} {x['exit']:10.3f} "
          f"{x['risk']:7.3f} {x['r']:+7.2f} {x['net']:+8.2f} "
          f"{x['hold']:6.0f}m {x['comment'][:16]:16s}{flag}")

print("\n" + "=" * 116)
print("HOW OFTEN DOES A METAL LOSS EXCEED 1R?")
print("=" * 116)
losers = [x for x in rows if x["r"] < 0]
blown = [x for x in losers if x["over"] > 0.15]
print(f"  losing metal trades: {len(losers)}")
print(f"  of which exceeded -1R by more than 0.15R: {len(blown)} "
      f"({100*len(blown)/len(losers) if losers else 0:.0f}%)")
if blown:
    print(f"  total overshoot cost: "
          f"{sum(x['over'] for x in blown):.2f}R")
    print("\n  the worst:")
    for x in sorted(blown, key=lambda z: -z["over"])[:8]:
        print(f"    {x['t0']:%m-%d %H:%M} {x['sym']:9s} {x['dir']:4s} "
              f"R={x['r']:+6.2f}  ${x['net']:+7.2f}  overshoot {x['over']:.2f}R  "
              f"held {x['hold']:.0f}m  '{x['comment'][:24]}'")

print("\n" + "=" * 116)
print("SAME CHECK ON CRYPTO - is the overshoot metal-specific?")
print("=" * 116)
mt5.initialize(timeout=10000)
deals2 = mt5.history_deals_get(START, datetime.now() + timedelta(hours=6)) or []
pos2 = defaultdict(list)
for d in deals2:
    if d.position_id and d.type in (0, 1):
        pos2[d.position_id].append(d)
crypto = []
for pid, ds in pos2.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    if not any(k in ins[0].symbol.upper() for k in ("BTC", "ETH")):
        continue
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    sl0 = d0.get("sl")
    if not sl0:
        continue
    entry = ins[0].price
    sign = 1 if ins[0].type == 0 else -1
    risk = abs(entry - sl0)
    if risk <= 0:
        continue
    r = sign * (outs[-1].price - entry) / risk
    crypto.append(r)
mt5.shutdown()
cl = [r for r in crypto if r < 0]
cb = [r for r in cl if abs(r) - 1.0 > 0.15]
print(f"  losing crypto trades: {len(cl)}")
print(f"  exceeded -1R by >0.15R: {len(cb)} ({100*len(cb)/len(cl) if cl else 0:.0f}%)")
print(f"  total overshoot: {sum(abs(r)-1.0 for r in cb):.2f}R")
