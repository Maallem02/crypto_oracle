"""Trades that die fast are the whole loss. WHY do they die?

Two candidate mechanisms, and they imply opposite fixes:

  (a) the trade never went green  -> entry was simply wrong-direction/wrong-time
      fix = entry timing, NOT stop width
  (b) it went green, then reversed through the stop -> stop sat inside noise
      fix = stop placement

Measure MFE (max favourable excursion) before the exit for fast losers. Also
split by entry mode: a resting LIMIT fills at a chosen price, a MARKET order
chases - if fast deaths concentrate in one mode that is a one-line fix.
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
    mfe = 0.0
    for b in (bars if bars is not None else []):
        f = ((b["high"] - entry) / risk if sign > 0 else (entry - b["low"]) / risk)
        mfe = max(mfe, f)
    T.append(dict(
        sym=ins[0].symbol.replace("USDm", "").replace("m", ""),
        net=sum(x.profit + x.commission + x.swap for x in ds),
        hold=(outs[-1].time - ins[0].time) / 60.0,
        mfe=mfe,
        mode="pending" if "-P" in (ins[0].comment or "") else "market",
        slip=abs(entry - (d0.get("entry") or entry)) / risk))
mt5.shutdown()
print(f"trades with a logged SL: {len(T)}\n")

print("=" * 96)
print("DID THE FAST LOSERS EVER GO GREEN?  (MFE in R before the exit)")
print("=" * 96)
print(f"  {'bucket':14s} {'n':>4s} {'medMFE':>8s} {'meanMFE':>8s} "
      f"{'never>0.2R':>11s} {'got>0.5R':>9s} {'got>1R':>8s}")
for lo, hi, lbl in [(0, 5, "under 5 min"), (5, 15, "5-15 min"),
                    (15, 60, "15-60 min"), (60, 240, "1-4 h"),
                    (240, 1e9, "over 4 h")]:
    v = [x for x in T if lo <= x["hold"] < hi and x["net"] <= 0]
    if not v:
        continue
    print(f"  {lbl:14s} {len(v):4d} {median(x['mfe'] for x in v):8.2f} "
          f"{mean(x['mfe'] for x in v):8.2f} "
          f"{100*sum(1 for x in v if x['mfe']<0.2)/len(v):10.0f}% "
          f"{100*sum(1 for x in v if x['mfe']>0.5)/len(v):8.0f}% "
          f"{100*sum(1 for x in v if x['mfe']>1.0)/len(v):7.0f}%")

print("\n" + "=" * 96)
print("ENTRY MODE - does one fill style die fast?")
print("=" * 96)
for mode in ("market", "pending"):
    v = [x for x in T if x["mode"] == mode]
    if not v:
        continue
    fast = [x for x in v if x["hold"] < 60]
    w = [x for x in v if x["net"] > 0]
    print(f"  {mode:8s} n={len(v):4d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(w)/len(v):5.1f}%  "
          f"median hold {median(x['hold'] for x in v):6.1f} min  "
          f"sub-1h share {100*len(fast)/len(v):4.0f}%  "
          f"sub-1h net ${sum(x['net'] for x in fast):+8.2f}")

print("\n  cross-tab: net P/L by mode x holding bucket")
print(f"  {'bucket':14s} {'market n':>9s} {'market $':>10s} "
      f"{'pending n':>10s} {'pending $':>10s}")
for lo, hi, lbl in [(0, 5, "under 5 min"), (5, 15, "5-15 min"),
                    (15, 60, "15-60 min"), (60, 240, "1-4 h"),
                    (240, 1e9, "over 4 h")]:
    m = [x for x in T if lo <= x["hold"] < hi and x["mode"] == "market"]
    p = [x for x in T if lo <= x["hold"] < hi and x["mode"] == "pending"]
    print(f"  {lbl:14s} {len(m):9d} {sum(x['net'] for x in m):+10.2f} "
          f"{len(p):10d} {sum(x['net'] for x in p):+10.2f}")
