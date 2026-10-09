"""Would a WIDER initial stop actually help metals?

The bucket test said no - but that test is confounded. Stop width in ATR is a
property of the SIGNAL, not a setting: a setup that produces a wide stop is a
different kind of setup. "Wide-stop trades did worse" therefore does not mean
"widening the stop would do worse".

The clean test is a REPLAY: take the exact same trades, move only the stop,
keep entry/TP/trail identical. That changes no selection, so there is no
counterfactual-counting bias.

Reported in R (risk-normalised) AND in dollars at the wider stop's real size,
because a wider stop at the 0.01 lot floor means MORE dollars at risk - metals
cannot be sized down to compensate.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

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

CONTRACT = {"XAU": 100.0, "XAG": 5000.0}

TR = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    sym = ins[0].symbol.upper()
    key = next((k for k in CONTRACT if k in sym), None)
    if not key:
        continue
    s = sig.get(pid)
    d0 = json.loads(s["data"]) if (s and s["data"]) else {}
    sl0, tp0, atr = d0.get("sl"), d0.get("tp1"), d0.get("atr")
    if not sl0 or not tp0 or not atr:
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    entry = ins[0].price
    is_buy = ins[0].type == 0
    risk0 = abs(entry - sl0)
    if risk0 <= 0:
        continue
    bars = mt5.copy_rates_range(ins[0].symbol, mt5.TIMEFRAME_M1, t0,
                                t0 + timedelta(hours=48))
    if bars is None or len(bars) < 5:
        continue
    TR.append(dict(key=key, sym=ins[0].symbol, entry=entry, sl0=sl0, tp0=tp0,
                   atr=atr, risk0=risk0, is_buy=is_buy, bars=bars,
                   vol=ins[0].volume))
mt5.shutdown()
print(f"metal trades replayable: {len(TR)}\n")

# live per-symbol trail config (router.py)
FLOOR = {"XAU": 1.5, "XAG": 0.0}
ARM = {"XAU": 1.0, "XAG": 1.0}


def sim(t, widen=1.0, be_r=1.5):
    """Replays router.py's exit rule with the initial stop scaled by `widen`."""
    entry, tp = t["entry"], t["tp0"]
    is_buy, atr = t["is_buy"], t["atr"]
    risk = t["risk0"] * widen
    sl = entry - risk if is_buy else entry + risk
    total = abs(tp - entry)
    floor_mult = FLOOR.get(t["key"], 0.0)
    start_r = ARM.get(t["key"], 1.0)
    for b in t["bars"]:
        hi, lo, close = b["high"], b["low"], b["close"]
        if is_buy and lo <= sl:
            return (sl - entry) / risk, risk
        if (not is_buy) and hi >= sl:
            return (entry - sl) / risk, risk
        if is_buy and hi >= tp:
            return (tp - entry) / risk, risk
        if (not is_buy) and lo <= tp:
            return (entry - tp) / risk, risk
        moved = (close - entry) if is_buy else (entry - close)
        if moved >= risk * start_r:
            buf = max(total * 0.05, moved * 0.10, floor_mult * atr)
            cand = close - buf if is_buy else close + buf
            if (is_buy and cand > sl) or ((not is_buy) and cand < sl):
                sl = cand
        elif be_r > 0:
            rn = (entry - sl) if is_buy else (sl - entry)
            if rn > 0 and moved >= rn * be_r:
                sl = entry
    last = t["bars"][-1]["close"]
    return (((last - entry) if is_buy else (entry - last)) / risk), risk


def run(sub, label):
    print(f"\n  {label}  (n={len(sub)})")
    print(f"    {'widen':>7s} {'totalR':>8s} {'avgR':>7s} {'WR':>6s} "
          f"{'$ total':>9s} {'med $risk':>10s} {'balance needed':>15s}")
    for w in (1.0, 1.25, 1.5, 1.75, 2.0, 2.5):
        rs, dollars, risks = [], 0.0, []
        for t in sub:
            r, risk = sim(t, widen=w)
            rs.append(r)
            dv = risk * t["vol"] * CONTRACT[t["key"]]
            risks.append(dv)
            dollars += r * dv
        wins = [r for r in rs if r > 0]
        risks.sort()
        med = risks[len(risks) // 2]
        # a stop this wide must still fit the 7% cap at the 0.01 lot floor
        need = max(risks) / 0.07
        mark = "   <- LIVE" if w == 1.0 else ""
        print(f"    {w:7.2f} {sum(rs):+8.1f} {mean(rs):+7.3f} "
              f"{100*len(wins)/len(rs):5.1f}% {dollars:+9.2f} {med:10.2f} "
              f"{need:14.0f}${mark}")


print("=" * 96)
print("REPLAY: SAME TRADES, WIDER INITIAL STOP")
print("=" * 96)
print("  'balance needed' = balance required for the WIDEST trade to clear the")
print("  7% cap at 0.01 lots. Metals cannot size down, so a wider stop costs")
print("  proportionally more dollars per trade.")
run(TR, "METALS (XAU + XAG)")
run([t for t in TR if t["key"] == "XAU"], "XAU only")
run([t for t in TR if t["key"] == "XAG"], "XAG only")

print("\n" + "=" * 96)
print("SANITY: the same replay on the LIVE setting should match reality")
print("=" * 96)
live = [sim(t, 1.0)[0] for t in TR]
print(f"  replayed avgR at widen=1.0: {mean(live):+.3f}")
