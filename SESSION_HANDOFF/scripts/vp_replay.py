"""Replay the Volume Profile strategy over real history. NO ORDERS are sent.

Uses the exact production code (profile.py, strategy.py and the runner's data
functions) so this also validates the live code path on real data.

Simulation rules, chosen to match live behaviour:
  - levels from the previous UTC session with bars (runner logic)
  - a signal is evaluated when its trigger M15 bar closes; entry at its close
  - session gate at the entry time: weekday, not 21:00-02:00 UTC
  - one open position per symbol (later signals skipped until it closes)
  - exit on M1 bars: stop or 2R target; if both are touched in the same
    minute, the STOP is assumed first (conservative); after 36 h, exit at
    market
  - spread cost from the bar's spread field, charged in R

NOT modelled: the shared slot with the SMC strategy, the lot/risk cap, slippage.
Treat the result as a sanity check with a small sample, not a verdict.
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import MetaTrader5 as mt5
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import atr_series, detect

UTC = timezone.utc
DAYS = 40
SYMBOLS = ([a for a in sys.argv[1:] if not a.startswith("--")]
           or ["BTCUSDm", "ETHUSDm", "XAGUSDm"])
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=DAYS)

results = []
blocked = defaultdict(int)
level_rows = []

for sym in SYMBOLS:
    si = mt5.symbol_info(sym)
    point = si.point
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, start - timedelta(days=5), now)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start - timedelta(days=5), now)
    if m1 is None or m15 is None or len(m1) < 1000:
        print(f"{sym}: not enough history")
        continue
    m15 = m15[m15["time"] + 900 <= now.timestamp()]          # completed bars only
    atr_all = atr_series(m15)
    m1_day = (m1["time"] // 86400)
    m15_day = (m15["time"] // 86400)
    days_with_bars = sorted(set(m1_day.tolist()))
    print(f"{sym}: {len(m1)} M1 bars from "
          f"{datetime.fromtimestamp(int(m1['time'][0]), UTC):%m-%d}, {len(m15)} M15 bars")

    open_until = 0                                           # epoch when the open trade ends
    for day in sorted(set(m15_day.tolist())):
        if day * 86400 < start.timestamp():
            continue
        prev = [d for d in days_with_bars if d < day]
        prev = [d for d in prev if (m1_day == d).sum() >= 60]
        if not prev:
            continue
        pday = prev[-1]
        prof = compute_profile(m1[m1_day == pday])
        if prof is None:
            continue
        level_rows.append((sym, datetime.fromtimestamp(pday * 86400, UTC).date(), prof))
        idx = np.where(m15_day == day)[0]
        tb, ta = m15[idx], atr_all[idx]
        for k in range(2, len(tb) + 1):
            sig = detect(tb[:k], ta[:k], prof)
            if sig is None:
                continue
            entry_t = sig.trigger_time + 900
            et = datetime.fromtimestamp(entry_t, UTC)
            # --free : heures du bot VP indépendant (aucune zone morte ni
            # blocage du week-end). Sinon : heures du bot SMC.
            if "--free" not in sys.argv and (et.weekday() >= 5 or et.hour >= 21 or et.hour < 2):
                continue
            session_tag = ("weekend" if et.weekday() >= 5 else
                           "night 21-02" if (et.hour >= 21 or et.hour < 2) else "day")
            if sig.blocked:
                blocked[f"{sig.setup}: {sig.blocked.split(':')[0]}"] += 1
                continue
            if entry_t < open_until:
                continue                                     # symbol busy
            fwd = m1[(m1["time"] >= entry_t) & (m1["time"] < entry_t + 36 * 3600)]
            buy = sig.direction == "buy"
            r_out, exit_t = None, entry_t
            for b in fwd:
                hit_sl = (b["low"] <= sig.sl) if buy else (b["high"] >= sig.sl)
                hit_tp = (b["high"] >= sig.tp) if buy else (b["low"] <= sig.tp)
                if hit_sl:
                    r_out, exit_t = -1.0, int(b["time"]); break
                if hit_tp:
                    r_out, exit_t = 2.0, int(b["time"]); break
            if r_out is None and len(fwd):
                last = fwd[-1]["close"]
                r_out = ((last - sig.entry) if buy else (sig.entry - last)) / sig.risk
                exit_t = int(fwd[-1]["time"])
            if r_out is None:
                continue                                     # too recent to resolve
            spread_r = float(tb[k - 1]["spread"]) * point / sig.risk
            open_until = exit_t
            results.append(dict(sym=sym.replace("USDm", ""), setup=sig.setup,
                                dir=sig.direction, t=et, r=r_out - spread_r,
                                spread_r=spread_r, risk_atr=sig.detail.get("risk_atr"),
                                session=session_tag))
mt5.shutdown()

print("\n" + "=" * 96)
print("SAMPLE OF THE LEVELS (last 3 sessions per symbol) - sanity check")
print("=" * 96)
for sym in SYMBOLS:
    for s, d, p in [x for x in level_rows if x[0] == sym][-3:]:
        rng = p.high - p.low
        print(f"  {s:8s} {d}  range {p.low:.5g}-{p.high:.5g}  POC {p.poc:.5g}  "
              f"VA {p.val:.5g}-{p.vah:.5g} ({100*(p.vah-p.val)/rng:.0f}% of range)  "
              f"closed {'inside' if p.closed_inside else 'OUTSIDE'}")


def rep(v, label):
    if not v:
        print(f"  {label:26s} n=  0")
        return
    w = [x for x in v if x["r"] > 0]
    gp = sum(x["r"] for x in w)
    gl = abs(sum(x["r"] for x in v if x["r"] <= 0))
    print(f"  {label:26s} n={len(v):3d}  WR={100*len(w)/len(v):5.1f}%  "
          f"total={sum(x['r'] for x in v):+6.1f}R  avg={np.mean([x['r'] for x in v]):+.3f}R  "
          f"PF={(gp/gl if gl else 9.99):5.2f}  spread={np.mean([x['spread_r'] for x in v]):.2f}R/trade")


print("\n" + "=" * 96)
print(f"REPLAY RESULTS, last {DAYS} days (after spread, 2R target, stop first on ties)")
print("=" * 96)
rep(results, "ALL SETUPS")
print("  breakeven win rate at 2R = 33.3%")
for s in ("poc_bounce", "va_reversal", "va_breakout"):
    rep([x for x in results if x["setup"] == s], f"  {s}")
print()
for s in sorted({x["sym"] for x in results}):
    rep([x for x in results if x["sym"] == s], f"  {s}")
print()
for s in ("day", "night 21-02", "weekend"):
    rep([x for x in results if x["session"] == s], f"  {s}")
print("\n  found but blocked by the risk sanity rules:")
for k, v in sorted(blocked.items(), key=lambda z: -z[1]):
    print(f"    {v:4d}  {k}")
print(f"\n  trades per day: {len(results)/DAYS:.1f}")
