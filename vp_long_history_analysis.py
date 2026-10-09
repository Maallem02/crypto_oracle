"""Analysis of data/binance_1m/vp_long_rows.json (vp_long_history_study.py). Read-only.
Live-like set = re-entries, room >= 1R, 1 position per symbol (next signal only
after the previous one closed), 2R, Exness-like spread paid."""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = timezone.utc
D = json.load(open(os.path.join(HERE, "data", "binance_1m", "vp_long_rows.json")))
R = sorted(D["rows"], key=lambda r: r["t"])
ts = lambda y, m, d=1: datetime(y, m, d, tzinfo=UTC).timestamp()
PERIODS = [("Q4 2023", ts(2023, 10), ts(2024, 1)), ("2024 H1", ts(2024, 1), ts(2024, 7)),
           ("2024 H2", ts(2024, 7), ts(2025, 1)), ("2025 H1", ts(2025, 1), ts(2025, 7)),
           ("2025 H2", ts(2025, 7), ts(2026, 1)), ("2026 H1", ts(2026, 1), ts(2026, 7)),
           ("2026 Q3+", ts(2026, 7), ts(2027, 1))]
weeks = (R[-1]["t"] - R[0]["t"]) / (7 * 86400)


def take(rows):
    busy, out = {}, []
    for r in rows:
        if busy.get(r["sym"], 0) > r["t"]:
            continue
        busy[r["sym"]] = r["x2"]
        out.append(r)
    return out


def st(rows, wk=None):
    if not rows:
        return "n=   0"
    r = np.array([x["r2"] for x in rows])
    gp, gl = r[r > 0].sum(), -r[r <= 0].sum()
    eq = np.cumsum(np.r_[0.0, r])
    dd = float((eq - np.maximum.accumulate(eq)).min())
    sk = cur = 0
    for v in r:
        cur = cur + 1 if v < 0 else 0
        sk = max(sk, cur)
    s = (f"n={len(r):4d} WR {100 * (r > 0).mean():3.0f}% avg {r.mean():+.3f} sum {r.sum():+6.1f}R "
         f"PF {gp / gl if gl else 9.9:4.2f} DD {dd:6.1f}R streak {sk:2d}")
    if wk:
        s += f" ({len(r) / wk:4.1f}/wk)"
    return s


def per_period(rows, label):
    cells = []
    for name, a, b in PERIODS:
        x = [r["r2"] for r in rows if a <= r["t"] < b]
        cells.append(f"{sum(x):+6.1f}")
    pos = sum(1 for name, a, b in PERIODS if sum(r["r2"] for r in rows if a <= r["t"] < b) > 0)
    return f"  {label:38s} " + " ".join(cells) + f"   positive {pos}/{len(PERIODS)}"


rev = [r for r in R if r["setup"] == "va_reversal"]
base = [r for r in rev if r["room"] >= 1.0]
first = lambda r: r["n_edge"] == 1
spread_ok = lambda r: r["spread_r"] < 0.10
VARIANTS = [("as live (room >= 1R)", lambda r: True), ("+ spread < 10%", spread_ok),
            ("+ first per edge", first), ("+ both filters", lambda r: spread_ok(r) and first(r))]

print(f"{len(R)} resolved signals, {len(rev)} re-entries, profiles weighted by '{D['weight']}', "
      f"{datetime.fromtimestamp(R[0]['t'], UTC):%d/%m/%Y} -> {datetime.fromtimestamp(R[-1]['t'], UTC):%d/%m/%Y}")

print("\n=== 1. RE-ENTRY, LIVE-LIKE SET, 3 YEARS (BTC + ETH) ===")
for lab, f in VARIANTS:
    print(f"  {lab:24s} {st(take([r for r in base if f(r)]), weeks)}")
print("\n  sum R per period:" + " " * 22 + " ".join(f"{n:>7s}" for n, _, _ in PERIODS))
for lab, f in VARIANTS:
    print(per_period(take([r for r in base if f(r)]), lab))
for s in ("BTCUSD", "ETHUSD"):
    for lab, f in (VARIANTS[0], VARIANTS[3]):
        print(per_period(take([r for r in base if f(r) and r["sym"] == s]), f"{s} {lab}"))

print("\n=== 2. IS THERE A DIRECTION EDGE? (+1R before -1R, every re-entry signal) ===")
print("  period       VP entries   opposite side   random times   n")
for name, a, b in PERIODS + [("ALL", 0, 9e12)]:
    rows = [r for r in rev if a <= r["t"] < b]
    if not rows:
        continue
    vp = 100 * np.mean([r["r1"] > 0 for r in rows])
    mi = 100 * np.mean([r["mirror"] > 0 for r in rows if r["mirror"] is not None])
    bs = 100 * np.mean([x > 0 for r in rows for x in r["base"]])
    print(f"  {name:10s}   {vp:5.1f}%       {mi:5.1f}%          {bs:5.1f}%       {len(rows)}")
for s in ("BTCUSD", "ETHUSD"):
    rows = [r for r in rev if r["sym"] == s]
    print(f"  {s}      {100 * np.mean([r['r1'] > 0 for r in rows]):5.1f}%       "
          f"{100 * np.mean([r['mirror'] > 0 for r in rows if r['mirror'] is not None]):5.1f}%          "
          f"{100 * np.mean([x > 0 for r in rows for x in r['base']]):5.1f}%       {len(rows)}")

print("\n=== 3. TREND MONTHS vs RANGE MONTHS (live-like, as live) ===")
sys.path.insert(0, HERE)
import vp_long_history_study as L
mret = {}
for s, pair in L.PAIRS.items():
    m1 = L.load(pair)
    mo = np.array([datetime.fromtimestamp(int(t), UTC).strftime("%Y-%m") for t in m1["time"][::1440]])
    closes = m1["close"][::1440]
    for k in np.unique(mo):
        c = closes[mo == k]
        mret[(s, k)] = abs(c[-1] / c[0] - 1) * 100
L1 = take(base)
by_m = {}
for r in L1:
    k = (r["sym"], datetime.fromtimestamp(r["t"], UTC).strftime("%Y-%m"))
    by_m.setdefault(k, []).append(r["r2"])
keys = [k for k in by_m if k in mret]
med = np.median([mret[k] for k in keys])
for lab, sel in (("trend months (|move| > median)", [k for k in keys if mret[k] > med]),
                 ("range months (|move| <= median)", [k for k in keys if mret[k] <= med])):
    x = [v for k in sel for v in by_m[k]]
    print(f"  {lab:34s} {len(sel):3d} months  {len(x):4d} trades  sum {sum(x):+6.1f}R  avg {np.mean(x):+.3f}R")
cc = np.corrcoef([mret[k] for k in keys], [sum(by_m[k]) for k in keys])[0, 1]
print(f"  correlation monthly |move| vs monthly R: {cc:+.2f}   (median monthly move {med:.1f}%)")

print("\n=== 4. THE OTHER SETUPS (every signal / 1 per symbol) ===")
for setup in ("va_breakout", "poc_bounce"):
    rows = [r for r in R if r["setup"] == setup]
    print(f"  {setup:12s} every signal   {st(rows)}")
    print(f"  {setup:12s} 1 per symbol   {st(take(rows), weeks)}")
    print(per_period(take(rows), f"{setup} 1 per symbol"))

print("\n=== 5. OVERLAP WITH THE EXNESS STUDY (01/08 -> 07/10/2026, live-like, as live) ===")
a = ts(2026, 8)
for s in ("BTCUSD", "ETHUSD"):
    print(f"  {s}: Binance {st(take([r for r in base if r['sym'] == s and r['t'] >= a]))}")
print("  (Exness study, same window: BTC +6R on 51 trades, ETH +1R on 47 trades)")
