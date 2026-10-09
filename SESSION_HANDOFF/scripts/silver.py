"""A considered view on XAG - the whole record, not the last 9 trades.

History:
  08-20  blocked  (24 trades, 20.8% WR, -63.35$, MFE 0.00 on the last 3)
  08-27  unblocked by user choice
  09-15  risk cap 7% -> 15%  (stops 1.09x -> 2.29x ATR)
  09-19  gold blocked -> silver alone in the metals breaker class

Questions:
  1. Lifetime and per-era performance - is the recent period different?
  2. Does the "low WR, big RR" profile still hold after the cap change?
  3. Direction, entry mode, session - anything that separates good from bad?
  4. Portfolio: what does silver contribute vs what it risks?
"""
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 6, 1),
                              datetime.now() + timedelta(hours=6)) or []
acct = mt5.account_info()
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

db = sqlite3.connect(
    r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
sig = {int(r["ticket"]): dict(r) for r in
       db.execute("select * from scalp_log where timestamp>='2026-05-25'")
       if r["ticket"]}
SL_RE = re.compile(r"\[sl\s+([0-9.]+)\]")

T = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    sym = ins[0].symbol.upper()
    key = ("XAG" if "XAG" in sym else "XAU" if "XAU" in sym else
           "BTC" if "BTC" in sym else "ETH" if "ETH" in sym else None)
    if not key:
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    t1 = datetime.fromtimestamp(outs[-1].time)
    entry = ins[0].price
    sign = 1 if ins[0].type == 0 else -1
    m = SL_RE.search(outs[-1].comment or "")
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    sl = float(m.group(1)) if m else d0.get("sl")
    risk = abs(entry - sl) if sl else None
    mfe = None
    if key == "XAG" and risk:
        bars = mt5.copy_rates_range(ins[0].symbol, mt5.TIMEFRAME_M1, t0, t1)
        mfe = 0.0
        for b in (bars if bars is not None else []):
            f = ((b["high"] - entry) if sign > 0 else (entry - b["low"])) / risk
            mfe = max(mfe, f)
    T.append(dict(t0=t0, key=key, dir="BUY" if sign > 0 else "SELL",
                  net=sum(x.profit + x.commission + x.swap for x in ds),
                  mode="pending" if "-P" in (ins[0].comment or "") else "market",
                  hold=(outs[-1].time - ins[0].time) / 60.0,
                  utc_h=datetime.fromtimestamp(ins[0].time, tz=timezone.utc).hour,
                  mfe=mfe))
mt5.shutdown()
T.sort(key=lambda x: x["t0"])
XAG = [x for x in T if x["key"] == "XAG"]


def rep(v, label, pad=30):
    if not v:
        print(f"  {label:<{pad}} n=  0")
        return
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    aw = mean(x["net"] for x in w) if w else 0
    al = abs(mean(x["net"] for x in l)) if l else 1
    be = 100 / (1 + aw / al) if aw else 100
    print(f"  {label:<{pad}} n={len(v):3d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(w)/len(v):5.1f}%  avgW=${aw:+6.2f}  avgL=${-al:+6.2f}  "
          f"RR={aw/al:4.2f}  needWR={be:4.1f}%  PF={(gp/gl if gl else 9.99):5.2f}")


print("=" * 118)
print("1. SILVER BY ERA")
print("=" * 118)
rep(XAG, "LIFETIME")
rep([x for x in XAG if x["t0"] < datetime(2026, 8, 20)], "  before the 08-20 block")
rep([x for x in XAG if datetime(2026, 8, 27) <= x["t0"] < datetime(2026, 9, 15, 22, 15)],
    "  unblocked, 7% cap (08-27 -> 09-15)")
rep([x for x in XAG if x["t0"] >= datetime(2026, 9, 15, 22, 15)],
    "  15% cap (since 09-15)")

print("\n  every silver trade since unblocking:")
run = 0.0
for x in [x for x in XAG if x["t0"] >= datetime(2026, 8, 27)]:
    run += x["net"]
    tag = "  [15% cap]" if x["t0"] >= datetime(2026, 9, 15, 22, 15) else ""
    print(f"    {x['t0']:%m-%d %H:%M} {x['dir']:4s} {x['mode']:8s} {x['net']:+7.2f}  "
          f"MFE {x['mfe'] if x['mfe'] is not None else 0:5.2f}R  "
          f"held {x['hold']:5.0f}m  run {run:+8.2f}{tag}")

print("\n" + "=" * 118)
print("2. WHAT SEPARATES SILVER WINNERS FROM LOSERS?  (lifetime)")
print("=" * 118)
for d in ("BUY", "SELL"):
    rep([x for x in XAG if x["dir"] == d], f"  {d}")
print()
for m in ("market", "pending"):
    rep([x for x in XAG if x["mode"] == m], f"  {m}")
print()
for lo, hi, lbl in [(2, 6, "02-06 UTC Asia"), (6, 9, "06-09 UTC pre-London"),
                    (9, 13, "09-13 UTC London"), (13, 17, "13-17 UTC London/NY"),
                    (17, 21, "17-21 UTC NY")]:
    rep([x for x in XAG if lo <= x["utc_h"] < hi], f"  {lbl}")

print("\n" + "=" * 118)
print("3. DID LOSERS EVER GO GREEN?  (silver MFE, since unblocking)")
print("=" * 118)
rec = [x for x in XAG if x["t0"] >= datetime(2026, 8, 27) and x["mfe"] is not None]
los = [x for x in rec if x["net"] <= 0]
win = [x for x in rec if x["net"] > 0]
if los:
    print(f"  losers  n={len(los):2d}  median MFE {median(x['mfe'] for x in los):.2f}R   "
          f"never reached 0.3R: {sum(1 for x in los if x['mfe'] < 0.3)}/{len(los)}")
if win:
    print(f"  winners n={len(win):2d}  median MFE {median(x['mfe'] for x in win):.2f}R")

print("\n" + "=" * 118)
print("4. SILVER vs THE REST OF THE PORTFOLIO  (since the 09-15 changes)")
print("=" * 118)
CUT = datetime(2026, 9, 15, 22, 15)
for k in ("BTC", "ETH", "XAG", "XAU"):
    rep([x for x in T if x["key"] == k and x["t0"] >= CUT], f"  {k}")
rep([x for x in T if x["t0"] >= CUT and x["key"] != "XAG"], "  portfolio WITHOUT silver")
rep([x for x in T if x["t0"] >= CUT], "  portfolio WITH silver")

print("\n" + "=" * 118)
print("5. RISK SILVER CARRIES AT TODAY'S BALANCE")
print("=" * 118)
bal = acct.balance
print(f"  balance ${bal:.2f}")
print(f"  silver cap 15%  -> max loss per trade ${bal*0.15:.2f}")
print(f"  crypto cap  7%  -> max loss per trade ${bal*0.07:.2f}")
sl_losses = [abs(x["net"]) for x in XAG if x["net"] < 0 and x["t0"] >= CUT]
if sl_losses:
    print(f"  actual silver losses since cap change: median ${median(sl_losses):.2f}, "
          f"worst ${max(sl_losses):.2f}")
