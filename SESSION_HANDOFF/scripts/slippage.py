"""Did metal stops REALLY get blown through, or is that my matching artifact?

On 2026-09-10 I reported a -21.68R gold loss as a risk breach. It was not: the
bot re-fired the same signal 8 minutes later and I paired the STALE scalp_log
SL with the NEWER fill, producing a fake 0.229 risk distance.

The broker's own exit comment "[sl <price>]" carries the stop that actually
fired. That is ground truth and cannot be mismatched. So:

    real_risk  = |entry - sl_from_exit_comment|
    honoured   = exit price == that sl (within a tick)
    slipped    = exit price BEYOND it  -> genuine gap/slippage

Then express the loss in DOLLARS against the dollar value of that real stop.
"""
import re
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

CONTRACT = {"XAU": 100.0, "XAG": 5000.0, "BTC": 1.0, "ETH": 1.0}
SL_RE = re.compile(r"\[sl\s+([0-9.]+)\]")

rows = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    m = SL_RE.search(outs[-1].comment or "")
    if not m:
        continue                      # closed by TP or manually - not a stop test
    sl = float(m.group(1))
    entry = ins[0].price
    exit_px = outs[-1].price
    sign = 1 if ins[0].type == 0 else -1
    real_risk = abs(entry - sl)
    if real_risk <= 0:
        continue
    sym = ins[0].symbol
    key = next((k for k in CONTRACT if k in sym.upper()), None)
    if not key:
        continue
    # how far PAST the stop did it actually fill?
    beyond = (sl - exit_px) if sign > 0 else (exit_px - sl)
    net = sum(x.profit + x.commission + x.swap for x in ds)
    expected_1r = real_risk * ins[0].volume * CONTRACT[key]
    rows.append(dict(
        t0=datetime.fromtimestamp(ins[0].time), sym=sym, key=key,
        dir="BUY" if sign > 0 else "SELL", entry=entry, sl=sl, exit=exit_px,
        real_risk=real_risk, beyond=beyond, net=net, expected_1r=expected_1r,
        ratio=(abs(net) / expected_1r) if expected_1r else 0,
        hold=(outs[-1].time - ins[0].time) / 60.0))
mt5.shutdown()
rows.sort(key=lambda x: x["t0"])

print("=" * 112)
print("STOP-OUTS MEASURED AGAINST THE BROKER'S OWN SL (exit comment)")
print("=" * 112)
print(f"  {'when':14s} {'sym':9s} {'dir':4s} {'entry':>10s} {'real SL':>10s} "
      f"{'fill':>10s} {'past SL':>8s} {'$ loss':>8s} {'vs 1R':>7s}")
for x in rows:
    if x["net"] > 0:
        continue
    flag = ""
    if x["ratio"] > 1.25:
        flag = "  <<< SLIPPED"
    print(f"  {x['t0']:%m-%d %H:%M}  {x['sym']:9s} {x['dir']:4s} "
          f"{x['entry']:10.3f} {x['sl']:10.3f} {x['exit']:10.3f} "
          f"{x['beyond']:+8.3f} {x['net']:+8.2f} {x['ratio']:6.2f}R{flag}")

print("\n" + "=" * 112)
print("VERDICT PER INSTRUMENT - are stops being honoured?")
print("=" * 112)
print(f"  {'group':10s} {'stopped':>8s} {'slipped >1.25R':>15s} "
      f"{'worst':>8s} {'extra $ lost':>13s}")
for grp, keys in (("METALS", ("XAU", "XAG")), ("CRYPTO", ("BTC", "ETH"))):
    v = [x for x in rows if x["key"] in keys and x["net"] <= 0]
    if not v:
        continue
    sl = [x for x in v if x["ratio"] > 1.25]
    extra = sum(abs(x["net"]) - x["expected_1r"] for x in sl)
    print(f"  {grp:10s} {len(v):8d} {len(sl):15d} "
          f"{max((x['ratio'] for x in v), default=0):7.2f}R {extra:+13.2f}")

print("\n  the genuinely slipped ones:")
for x in sorted([r for r in rows if r["net"] <= 0 and r["ratio"] > 1.25],
                key=lambda z: -z["ratio"]):
    print(f"    {x['t0']:%m-%d %H:%M} {x['sym']:9s} {x['dir']:4s} "
          f"stop {x['sl']:.3f} -> filled {x['exit']:.3f} "
          f"({x['beyond']:+.3f} past)  ${x['net']:+.2f} vs ${x['expected_1r']:.2f} "
          f"expected  held {x['hold']:.0f}m")
