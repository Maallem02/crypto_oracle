"""Pendings that die in <15 min cost $159.67 and 86% never went 0.2R green.

Hypothesis: the bot COMPRESSES the pending entry toward the SL to fit the 7%
risk cap. A compressed limit sits deep inside the move, close to its own stop,
so the sweep that fills it is already most of the way to stopping it out.

Test: for pending fills, measure how far the FILLED price sits from the SL as a
fraction of the signal's ORIGINAL risk. Compressed = ratio well under 1.0.
Then see whether compressed fills die faster and lose more.
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
    s = sig.get(pid)
    if not s or not s["data"]:
        continue
    d0 = json.loads(s["data"])
    sl0 = d0.get("sl")
    sig_entry = d0.get("entry") or d0.get("price")
    if not sl0 or not sig_entry:
        continue
    fill = ins[0].price
    orig_risk = abs(sig_entry - sl0)
    actual_risk = abs(fill - sl0)
    if orig_risk <= 0:
        continue
    T.append(dict(
        sym=ins[0].symbol.replace("USDm", "").replace("m", ""),
        net=sum(x.profit + x.commission + x.swap for x in ds),
        hold=(outs[-1].time - ins[0].time) / 60.0,
        ratio=actual_risk / orig_risk,          # <1 = compressed toward SL
        mode="pending" if "-P" in (ins[0].comment or "") else "market"))
mt5.shutdown()
print(f"trades with both signal entry and SL logged: {len(T)}\n")


def rep(v, label):
    if not v:
        print(f"  {label:26s} n=  0")
        return
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    print(f"  {label:26s} n={len(v):4d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"avg=${mean(x['net'] for x in v):+6.2f}  WR={100*len(w)/len(v):5.1f}%  "
          f"medHold={median(x['hold'] for x in v):6.1f}m  "
          f"PF={(gp/gl if gl else 9.99):5.2f}")


print("=" * 100)
print("ENTRY COMPRESSION: filled risk as a fraction of the SIGNAL's risk")
print("=" * 100)
print("  ratio < 1.0 means the fill sits CLOSER to the SL than the signal intended\n")
for lo, hi, lbl in [(0, 0.5, "heavily compressed <0.5"),
                    (0.5, 0.8, "compressed 0.5-0.8"),
                    (0.8, 1.05, "as designed 0.8-1.05"),
                    (1.05, 1.5, "wider 1.05-1.5"),
                    (1.5, 99, "much wider >1.5")]:
    rep([x for x in T if lo <= x["ratio"] < hi], lbl)

print("\n  PENDING fills only:")
P = [x for x in T if x["mode"] == "pending"]
for lo, hi, lbl in [(0, 0.5, "heavily compressed <0.5"),
                    (0.5, 0.8, "compressed 0.5-0.8"),
                    (0.8, 1.05, "as designed 0.8-1.05"),
                    (1.05, 99, "wider >1.05")]:
    rep([x for x in P if lo <= x["ratio"] < hi], lbl)

print("\n  MARKET fills only:")
M = [x for x in T if x["mode"] == "market"]
for lo, hi, lbl in [(0, 0.5, "heavily compressed <0.5"),
                    (0.5, 0.8, "compressed 0.5-0.8"),
                    (0.8, 1.05, "as designed 0.8-1.05"),
                    (1.05, 99, "wider >1.05")]:
    rep([x for x in M if lo <= x["ratio"] < hi], lbl)

print("\n" + "=" * 100)
print("DO COMPRESSED FILLS DIE FASTER?")
print("=" * 100)
comp = [x for x in T if x["ratio"] < 0.8]
norm = [x for x in T if x["ratio"] >= 0.8]
for v, lbl in ((comp, "compressed (<0.8)"), (norm, "not compressed (>=0.8)")):
    if not v:
        continue
    fast = [x for x in v if x["hold"] < 15]
    print(f"  {lbl:24s} n={len(v):4d}  "
          f"died <15min: {100*len(fast)/len(v):4.0f}%  "
          f"their net ${sum(x['net'] for x in fast):+8.2f}")
