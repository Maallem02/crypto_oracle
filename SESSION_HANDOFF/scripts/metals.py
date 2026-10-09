"""Are metal ENTRIES wrong, or is something after the entry wrong?

The test is MFE - how far a trade went in your favour before it died.

  MFE ~ 0       -> the entry was wrong. Price never agreed, not once.
  MFE >= 1R     -> the entry was RIGHT and the exit gave it back.

Also measure MAE in the first 15 minutes: an entry that is immediately deep
underwater was taken mid-move rather than at a level.

Compare metals against ETH/BTC on the same yardstick, and split lifetime vs
recent - XAU is PF 0.97 lifetime but 0.36 since 09-01, so something CHANGED
rather than being wrong all along.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
START = datetime(2026, 7, 27)
deals = mt5.history_deals_get(START, datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

db = sqlite3.connect(
    r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
sig = {int(r["ticket"]): dict(r) for r in
       db.execute("select * from scalp_log where timestamp>='2026-07-25'")
       if r["ticket"]}

T = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    t1 = datetime.fromtimestamp(outs[-1].time)
    if t0 < START:
        continue
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    sl0 = d0.get("sl")
    entry = ins[0].price
    sign = 1 if ins[0].type == 0 else -1
    risk = abs(entry - sl0) if sl0 else None
    if not risk or risk <= 0:
        continue
    bars = mt5.copy_rates_range(ins[0].symbol, mt5.TIMEFRAME_M1, t0, t1)
    bars = list(bars) if bars is not None else []
    mfe = mae = 0.0
    mae15 = mfe15 = 0.0
    for b in bars:
        up = ((b["high"] - entry) if sign > 0 else (entry - b["low"])) / risk
        dn = ((b["low"] - entry) if sign > 0 else (entry - b["high"])) / risk
        mfe = max(mfe, up)
        mae = min(mae, dn)
        if b["time"] - ins[0].time <= 900:        # first 15 minutes
            mfe15 = max(mfe15, up)
            mae15 = min(mae15, dn)
    T.append(dict(
        t0=t0, sym=ins[0].symbol.replace("USDm", "").replace("m", ""),
        dir="buy" if sign > 0 else "sell",
        net=sum(x.profit + x.commission + x.swap for x in ds),
        r=sign * (outs[-1].price - entry) / risk,
        mfe=mfe, mae=mae, mfe15=mfe15, mae15=mae15,
        hold=(outs[-1].time - ins[0].time) / 60.0,
        hour=t0.hour,
        atr=d0.get("atr"), risk=risk,
        mode="pending" if "-P" in (ins[0].comment or "") else "market"))
mt5.shutdown()
print(f"trades analysable: {len(T)}\n")


def mfe_block(v, label):
    if not v:
        print(f"  {label:26s} n=  0")
        return
    l = [x for x in v if x["net"] <= 0]
    print(f"  {label:26s} n={len(v):4d}  "
          f"medMFE={median(x['mfe'] for x in v):5.2f}R  "
          f"losers medMFE={median(x['mfe'] for x in l) if l else 0:5.2f}R  "
          f"losers never>0.3R: {100*sum(1 for x in l if x['mfe']<0.3)/len(l) if l else 0:3.0f}%  "
          f"losers got>1R: {100*sum(1 for x in l if x['mfe']>1.0)/len(l) if l else 0:3.0f}%")


print("=" * 104)
print("WERE THE ENTRIES WRONG?  MFE = how far it went your way before dying")
print("=" * 104)
for s in ("XAU", "XAG", "ETH", "BTC"):
    mfe_block([x for x in T if x["sym"] == s], s)
print()
mfe_block([x for x in T if x["sym"] in ("XAU", "XAG")], "METALS")
mfe_block([x for x in T if x["sym"] in ("ETH", "BTC")], "CRYPTO")

print("\n" + "=" * 104)
print("IMMEDIATE PAIN: how deep underwater in the FIRST 15 MINUTES?")
print("=" * 104)
print(f"  {'sym':8s} {'n':>4s} {'med MAE15':>10s} {'med MFE15':>10s} "
      f"{'>0.5R against in 15m':>21s}")
for s in ("XAU", "XAG", "ETH", "BTC"):
    v = [x for x in T if x["sym"] == s]
    if not v:
        continue
    bad = sum(1 for x in v if x["mae15"] < -0.5)
    print(f"  {s:8s} {len(v):4d} {median(x['mae15'] for x in v):+10.2f} "
          f"{median(x['mfe15'] for x in v):+10.2f} {100*bad/len(v):20.0f}%")

print("\n" + "=" * 104)
print("WHAT CHANGED?  metals, before vs after 09-01")
print("=" * 104)


def perf(v, label):
    if not v:
        print(f"  {label:30s} n=  0")
        return
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    print(f"  {label:30s} n={len(v):3d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(w)/len(v):5.1f}%  medMFE={median(x['mfe'] for x in v):5.2f}R  "
          f"avgR={mean(x['r'] for x in v):+5.2f}  PF={(gp/gl if gl else 9.99):5.2f}")


for s in ("XAU", "XAG"):
    print(f"\n  {s}:")
    perf([x for x in T if x["sym"] == s and x["t0"] < datetime(2026, 9, 1)],
         "  before 09-01")
    perf([x for x in T if x["sym"] == s and x["t0"] >= datetime(2026, 9, 1)],
         "  since 09-01")

print("\n" + "=" * 104)
print("METALS BY DIRECTION AND BY ENTRY MODE")
print("=" * 104)
met = [x for x in T if x["sym"] in ("XAU", "XAG")]
for d in ("buy", "sell"):
    perf([x for x in met if x["dir"] == d], f"metals {d}")
for m in ("market", "pending"):
    perf([x for x in met if x["mode"] == m], f"metals {m}")

print("\n" + "=" * 104)
print("METALS BY HOUR (UTC+1 local) - is it entering at the wrong TIME?")
print("=" * 104)
for lo, hi, lbl in [(2, 7, "02-07 Asia"), (7, 10, "07-10 pre-London"),
                    (10, 13, "10-13 London"), (13, 16, "13-16 London/NY"),
                    (16, 19, "16-19 NY"), (19, 22, "19-22 late NY")]:
    perf([x for x in met if lo <= x["hour"] < hi], lbl)
