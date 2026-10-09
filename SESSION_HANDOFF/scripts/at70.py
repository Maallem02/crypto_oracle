"""What does the bot actually do on a $70 account?

Risk caps are % of balance, but lot sizes are FIXED (BTC 0.01, ETH 0.45,
XAG 0.01) and metals/BTC are already at the broker minimum. So on a smaller
balance, the dollar risk per trade does not shrink - the CAP does. Trades
whose planned risk exceeds the cap are REJECTED.

Replay every trade since the 09-15 changes: planned dollar risk at entry
(stop distance x lot x contract), and whether it would clear the cap at
$70 vs the ~$150-190 it actually traded on.
"""
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

import MetaTrader5 as mt5

CUT = datetime(2026, 9, 15, 22, 15)
CAP = {"XAU": 0.15, "XAG": 0.15, "BTC": 0.07, "ETH": 0.07}
CONTRACT = {"XAU": 100.0, "XAG": 5000.0, "BTC": 1.0, "ETH": 1.0}
SL_RE = re.compile(r"\[sl\s+([0-9.]+)\]")

mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(CUT - timedelta(days=1),
                              datetime.now() + timedelta(hours=6)) or []
mt5.shutdown()
db = sqlite3.connect(
    r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
sig = {int(r["ticket"]): dict(r) for r in
       db.execute("select * from scalp_log where timestamp>='2026-09-10'")
       if r["ticket"]}

pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

rows = []
for pid, ds in pos.items():
    ds.sort(key=lambda z: z.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    if t0 < CUT:
        continue
    s = ins[0].symbol.upper()
    key = next((k for k in CONTRACT if k in s), None)
    if not key:
        continue
    m = SL_RE.search(outs[-1].comment or "")
    d0 = json.loads(sig[pid]["data"]) if pid in sig and sig[pid]["data"] else {}
    sl = float(m.group(1)) if m else d0.get("sl")
    net = sum(x.profit + x.commission + x.swap for x in ds)
    if sl is None or (m is None and not d0.get("sl")):
        risk_usd = abs(net) if net < 0 else None
    else:
        risk_usd = abs(ins[0].price - sl) * ins[0].volume * CONTRACT[key]
    # a trailed winner's exit SL is ABOVE entry - that isn't the entry risk.
    # fall back to the signal's original SL when the exit SL is in profit.
    if net > 0 and d0.get("sl"):
        risk_usd = abs(ins[0].price - d0["sl"]) * ins[0].volume * CONTRACT[key]
    rows.append(dict(t0=t0, key=key, net=net, risk=risk_usd))

print("=" * 96)
print("TRADES SINCE THE 09-15 CHANGES - WOULD THEY PASS THE CAP AT $70?")
print("=" * 96)
print(f"  caps at $70:  BTC/ETH 7% = ${70*0.07:.2f}    XAG/XAU 15% = ${70*0.15:.2f}\n")
print(f"  {'sym':4s} {'n':>3s} {'med risk$':>10s} {'cap@$70':>8s} "
      f"{'pass@$70':>9s} {'P/L of passing':>15s} {'P/L of rejected':>16s}")
tot_pass = tot_rej = 0.0
for k in ("BTC", "ETH", "XAG", "XAU"):
    v = [r for r in rows if r["key"] == k and r["risk"]]
    if not v:
        continue
    cap = 70 * CAP[k]
    ok = [r for r in v if r["risk"] <= cap]
    no = [r for r in v if r["risk"] > cap]
    tot_pass += sum(r["net"] for r in ok)
    tot_rej += sum(r["net"] for r in no)
    print(f"  {k:4s} {len(v):3d} {median(r['risk'] for r in v):10.2f} {cap:8.2f} "
          f"{len(ok):4d}/{len(v):<4d} {sum(r['net'] for r in ok):+15.2f} "
          f"{sum(r['net'] for r in no):+16.2f}")
print(f"\n  what the bot kept at $70: ${tot_pass:+.2f}   what the cap would have "
      f"thrown away: ${tot_rej:+.2f}")

print("\n" + "=" * 96)
print("ONE LOSING TRADE, AS A SHARE OF A $70 ACCOUNT")
print("=" * 96)
for k in ("BTC", "ETH", "XAG"):
    l = [abs(r["net"]) for r in rows if r["key"] == k and r["net"] < 0]
    if l:
        print(f"  {k:4s} typical loss ${median(l):5.2f} = {100*median(l)/70:4.1f}% of $70   "
              f"worst ${max(l):5.2f} = {100*max(l)/70:4.1f}%")

print("\n" + "=" * 96)
print("THE SURVIVAL HALT AT $70  (-35%  ->  stops at $45.50)")
print("=" * 96)
for k, loss in (("silver", 10.50), ("crypto", 4.90)):
    b, n = 70.0, 0
    while b > 45.5:
        b -= loss
        n += 1
    print(f"  {k:7s} losses of ${loss:5.2f} (full cap): {n} in a row trips the halt")
print("  silver's current streak: 4 losses in a row (Fri + three today)")
