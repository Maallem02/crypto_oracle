"""Compression was not the mechanism (96 normally-placed pendings still lost
$120). So why do resting limits get picked off?

Candidate: STALENESS. pending_max_age_minutes is 480 - a limit can rest 8 hours
and then be filled by whatever finally sweeps that level, long after the SMC
context that justified it has expired.

Test: time from order placement to fill, vs outcome. If limits that rest longer
do worse, pending_max_age_minutes is a one-number fix.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
START = datetime(2026, 7, 27)
END = datetime.now() + timedelta(hours=6)

orders = mt5.history_orders_get(START, END) or []
deals = mt5.history_deals_get(START, END) or []
mt5.shutdown()

# map position_id -> entry deal and full P/L
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

PEND_TYPES = {2: "buy_limit", 3: "sell_limit", 4: "buy_stop", 5: "sell_stop"}

# history_orders_get returns position_id == 0, so bridge through the deals:
# the entry deal carries .order (the originating order ticket) and .position_id
order_to_pos = {}
for d in deals:
    if d.entry == 0 and d.order and d.position_id:
        order_to_pos[d.order] = d.position_id

rows = []
for o in orders:
    if o.type not in PEND_TYPES:
        continue
    if o.state != 4:            # ORDER_STATE_FILLED
        continue
    pid = order_to_pos.get(o.ticket)
    ds = pos.get(pid) if pid else None
    if not ds:
        continue
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    rest = (o.time_done - o.time_setup) / 60.0
    rows.append(dict(
        sym=o.symbol.replace("USDm", "").replace("m", ""),
        kind=PEND_TYPES[o.type],
        rest=rest,
        hold=(outs[-1].time - ins[0].time) / 60.0,
        net=sum(x.profit + x.commission + x.swap for x in ds)))

print(f"filled pending orders matched to a position: {len(rows)}\n")


def rep(v, label):
    if not v:
        print(f"  {label:24s} n=  0")
        return
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    print(f"  {label:24s} n={len(v):4d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"avg=${mean(x['net'] for x in v):+6.2f}  WR={100*len(w)/len(v):5.1f}%  "
          f"medHold={median(x['hold'] for x in v):6.1f}m  "
          f"PF={(gp/gl if gl else 9.99):5.2f}")


print("=" * 100)
print("HOW LONG DID THE LIMIT REST BEFORE IT FILLED?")
print("=" * 100)
for lo, hi, lbl in [(0, 2, "filled <2 min"), (2, 10, "2-10 min"),
                    (10, 30, "10-30 min"), (30, 60, "30-60 min"),
                    (60, 180, "1-3 h"), (180, 480, "3-8 h"),
                    (480, 1e9, "over 8 h")]:
    rep([x for x in rows if lo <= x["rest"] < hi], lbl)

print("\n" + "=" * 100)
print("WHAT WOULD A SHORTER EXPIRY HAVE DONE?")
print("=" * 100)
base = sum(x["net"] for x in rows)
print(f"  current (480 min): {len(rows)} fills, net ${base:+.2f}\n")
print(f"  {'expiry':>10s} {'fills kept':>11s} {'net kept':>10s} "
      f"{'cancelled':>10s} {'their net':>10s} {'delta':>9s}")
for cap in (5, 10, 15, 30, 45, 60, 90, 120, 180, 240, 480):
    kept = [x for x in rows if x["rest"] <= cap]
    cut = [x for x in rows if x["rest"] > cap]
    print(f"  {cap:8d}m {len(kept):11d} {sum(x['net'] for x in kept):+10.2f} "
          f"{len(cut):10d} {sum(x['net'] for x in cut):+10.2f} "
          f"{sum(x['net'] for x in kept)-base:+9.2f}")

print("\n" + "=" * 100)
print("BY ORDER KIND")
print("=" * 100)
for k in sorted({x["kind"] for x in rows}):
    rep([x for x in rows if x["kind"] == k], k)

print("\n  per symbol:")
for s in sorted({x["sym"] for x in rows}):
    rep([x for x in rows if x["sym"] == s], s)
