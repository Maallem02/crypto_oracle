"""The robust metal finding: 61% of XAG trades go >0.5R against within 15 min
(XAU 44%) vs 16-25% for crypto - yet their median MFE is still ~0.6R. The
direction is fine; the stop sits inside the noise.

Hypothesis for WHY it is metal-specific: minimum lot size. Metals cannot be
sized below 0.01 lots, and 0.01 lot of gold is 1 oz ($1 per $1 move), 0.01 of
silver is 50 oz ($50 per $1 move). With a 7% cap the stop DISTANCE is forced
small. Crypto has no such floor in practice.

Measure the stop width in ATR units per symbol. If metals sit near 1xATR and
crypto near 2xATR, the cap - not the strategy - is choosing metal stops.
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

CONTRACT = {"XAU": 100.0, "XAG": 5000.0, "BTC": 1.0, "ETH": 1.0}

T = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    sl0, atr = d0.get("sl"), d0.get("atr")
    if not sl0 or not atr or atr <= 0:
        continue
    sym = ins[0].symbol.upper()
    key = next((k for k in CONTRACT if k in sym), None)
    if not key:
        continue
    entry = ins[0].price
    risk = abs(entry - sl0)
    if risk <= 0:
        continue
    T.append(dict(key=key, risk=risk, atr=atr, in_atr=risk / atr,
                  vol=ins[0].volume,
                  dollar_risk=risk * ins[0].volume * CONTRACT[key],
                  net=sum(x.profit + x.commission + x.swap for x in ds)))
mt5.shutdown()

print("=" * 96)
print("HOW WIDE IS THE STOP, IN ATR UNITS?")
print("=" * 96)
print(f"  {'sym':6s} {'n':>4s} {'med ATR':>10s} {'med stop':>10s} "
      f"{'stop/ATR':>9s} {'med $risk':>10s} {'lot':>6s}")
for k in ("XAU", "XAG", "ETH", "BTC"):
    v = [x for x in T if x["key"] == k]
    if not v:
        continue
    print(f"  {k:6s} {len(v):4d} {median(x['atr'] for x in v):10.4f} "
          f"{median(x['risk'] for x in v):10.4f} "
          f"{median(x['in_atr'] for x in v):9.2f} "
          f"{median(x['dollar_risk'] for x in v):10.2f} "
          f"{median(x['vol'] for x in v):6.2f}")

print("\n" + "=" * 96)
print("DISTRIBUTION OF stop/ATR - are metals pinned at the low end?")
print("=" * 96)
for k in ("XAU", "XAG", "ETH", "BTC"):
    v = [x["in_atr"] for x in T if x["key"] == k]
    if not v:
        continue
    v.sort()
    def q(p):
        return v[min(len(v) - 1, int(len(v) * p))]
    print(f"  {k:6s} n={len(v):3d}   p10={q(0.10):5.2f}  p25={q(0.25):5.2f}  "
          f"p50={q(0.50):5.2f}  p75={q(0.75):5.2f}  p90={q(0.90):5.2f}")

print("\n" + "=" * 96)
print("WHAT DOES THE 7% CAP ALLOW AT MINIMUM LOT?  (balance $141.64)")
print("=" * 96)
BAL = 141.64
CAP = BAL * 0.07
print(f"  cap = ${CAP:.2f}\n")
print(f"  {'sym':6s} {'min lot':>8s} {'$ per 1.0 move':>15s} "
      f"{'max stop (price)':>17s} {'median ATR':>11s} {'max stop in ATR':>16s}")
for k, minlot in (("XAU", 0.01), ("XAG", 0.01), ("ETH", 0.10), ("BTC", 0.01)):
    v = [x for x in T if x["key"] == k]
    if not v:
        continue
    per_unit = minlot * CONTRACT[k]
    max_stop = CAP / per_unit
    matr = median(x["atr"] for x in v)
    print(f"  {k:6s} {minlot:8.2f} {per_unit:15.2f} {max_stop:17.4f} "
          f"{matr:11.4f} {max_stop/matr:16.2f}")

print("\n  -> if 'max stop in ATR' is near 1, the RISK CAP is choosing the stop,")
print("     not the strategy. Below ~1.5xATR a stop sits inside normal noise.")

print("\n" + "=" * 96)
print("DOES A WIDER STOP ACTUALLY HELP METALS?  (outcome by stop/ATR bucket)")
print("=" * 96)
met = [x for x in T if x["key"] in ("XAU", "XAG")]
for lo, hi in ((0, 0.75), (0.75, 1.0), (1.0, 1.25), (1.25, 1.75), (1.75, 99)):
    v = [x for x in met if lo <= x["in_atr"] < hi]
    if not v:
        continue
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    print(f"  stop {lo:4.2f}-{hi:<5.2f}xATR  n={len(v):3d}  "
          f"net=${sum(x['net'] for x in v):+8.2f}  WR={100*len(w)/len(v):5.1f}%  "
          f"PF={(gp/gl if gl else 9.99):5.2f}")
