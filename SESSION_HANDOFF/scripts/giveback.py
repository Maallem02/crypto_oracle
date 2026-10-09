"""Peak equity hit $228.74; balance is $193.74. Where did ~$35 of open profit go?

For each trade this week: MFE (best open profit, in $) vs what was banked.
The trail is SUPPOSED to give some back - that's the price of letting
winners run. The question is how much, and on which symbol.

ETH's trail was changed 09-11 (arms at 1.5R, 3.0xATR floor). This is the
first week with enough ETH/BTC runners to see it working.

Also reconcile the balance properly: the -$7.05 BTC stop-out on Sun 03:45
was already inside the $128.14 Sunday figure.
"""
from collections import defaultdict
from datetime import datetime, timedelta

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
acct = mt5.account_info()
deals = mt5.history_deals_get(datetime(2026, 9, 18),
                              datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

print("=" * 104)
print("BALANCE RECONCILIATION")
print("=" * 104)
wk = [d for d in deals if d.type in (0, 1)
      and datetime.fromtimestamp(d.time) >= datetime(2026, 9, 20, 4, 0)]
tr = sum(d.profit + d.commission + d.swap for d in wk)
print(f"  Sunday balance (after the Sun 03:45 BTC stop-out)   $128.14")
print(f"  trading P/L after that                               ${tr:+.2f}")
print(f"  = ${128.14 + tr:.2f}      MT5 balance ${acct.balance:.2f}")

print("\n" + "=" * 104)
print("GIVE-BACK: best open profit vs banked, per trade")
print("=" * 104)
print(f"  {'opened':15s} {'sym':9s} {'dir':4s} {'held':>6s} {'peak $':>8s} "
      f"{'banked $':>9s} {'gave back':>10s} {'kept':>6s}")
rows = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    t1 = datetime.fromtimestamp(outs[-1].time)
    if t1 < datetime(2026, 9, 21):
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    sym = ins[0].symbol
    si = mt5.symbol_info(sym)
    cs = si.trade_contract_size if si else 1.0
    vol = ins[0].volume
    entry = ins[0].price
    sign = 1 if ins[0].type == 0 else -1
    bars = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, t0, t1)
    peak = 0.0
    for b in (bars if bars is not None else []):
        fav = (b["high"] - entry) if sign > 0 else (entry - b["low"])
        peak = max(peak, fav * vol * cs)
    net = sum(x.profit + x.commission + x.swap for x in ds)
    rows.append(dict(t0=t0, sym=sym, dir="BUY" if sign > 0 else "SELL",
                     hold=(outs[-1].time - ins[0].time) / 60.0,
                     peak=peak, net=net))
mt5.shutdown()
rows.sort(key=lambda z: z["t0"])
for r in rows:
    kept = 100 * r["net"] / r["peak"] if r["peak"] > 0.5 else 0
    print(f"  {r['t0']:%a %m-%d %H:%M} {r['sym']:9s} {r['dir']:4s} "
          f"{r['hold']:5.0f}m {r['peak']:8.2f} {r['net']:+9.2f} "
          f"{r['peak'] - r['net']:10.2f} {kept:5.0f}%")

win = [r for r in rows if r["net"] > 0]
print(f"\n  winners: {len(win)}   total peak ${sum(r['peak'] for r in win):.2f}   "
      f"banked ${sum(r['net'] for r in win):.2f}   "
      f"capture {100*sum(r['net'] for r in win)/sum(r['peak'] for r in win):.0f}%")
for key in ("BTC", "ETH"):
    v = [r for r in win if key in r["sym"].upper()]
    if v:
        print(f"    {key}: peak ${sum(r['peak'] for r in v):6.2f}  "
              f"banked ${sum(r['net'] for r in v):6.2f}  "
              f"capture {100*sum(r['net'] for r in v)/sum(r['peak'] for r in v):3.0f}%")
print("\n  previous capture rate (from the 09-11 study, all winners): 75%")
