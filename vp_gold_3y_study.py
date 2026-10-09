"""VP re-entry on GOLD over 3 years of the broker's own 1-minute data. Read-only.

Possible since 09/10/2026: MT5 "Max bars in chart" = 5,000,000 (set by the user),
so Exness serves M1 history for XAUUSDm back to 2022. This is the bot's exact
method, unlike the 16-month M5 approximation (vp_exness_m5_study.py):
  * previous-session profile from M1 tick volume (last UTC day with >= 60 M1 bars)
  * production detector (va_reversal) on MT5 M15 bars, entry at the trigger close
    + that bar's spread
  * stop / 2R resolved on M1 with each bar's spread, stop first on ties, 96 h max
  * the executor's forbidden windows for metals (Monday < 02:00, 23:45-00:30)
  * live-like set: room >= 1R, 1 position at a time
Not modelled: news pause / news close, the risk-cap rejections of a small account.

History is fetched in monthly chunks with pauses, so the terminal the live bots
use is never asked for a million bars at once."""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import MetaTrader5 as mt5
from features.volume_profile import strategy as S
from features.volume_profile.profile import compute_profile

UTC = timezone.utc
SYM = "XAUUSDm"
START = datetime(2023, 10, 1, tzinfo=UTC)
MAX_HOLD = 96 * 3600
OUT = os.path.join(HERE, "data", "vp_gold_3y_rows.json")
rng = np.random.default_rng(5)


def fetch(tf, start, end, step_days):
    parts, t = [], start
    while t < end:
        r = mt5.copy_rates_range(SYM, tf, t, min(t + timedelta(days=step_days), end))
        if r is not None and len(r):
            parts.append(r)
        t += timedelta(days=step_days)
        time.sleep(0.3)
    a = np.concatenate(parts)
    _, idx = np.unique(a["time"], return_index=True)
    return a[idx]


def forbidden(t):
    d = datetime.fromtimestamp(t, UTC)
    return (d.weekday() == 0 and d.hour < 2) or (d.hour == 23 and d.minute >= 45) or (d.hour == 0 and d.minute < 30)


mt5.initialize(timeout=10000)
NOW = datetime.now(UTC)
PT = mt5.symbol_info(SYM).point
m1 = fetch(mt5.TIMEFRAME_M1, START - timedelta(days=7), NOW, 30)
m15 = fetch(mt5.TIMEFRAME_M15, START - timedelta(days=30), NOW, 120)
mt5.shutdown()
m15 = m15[m15["time"] + 900 <= NOW.timestamp()]
atr = S.atr_series(m15)
print(f"{SYM}: {len(m1):,} M1 bars from {datetime.fromtimestamp(int(m1['time'][0]), UTC):%d/%m/%Y}, "
      f"{len(m15):,} M15 bars; largest M1 gap on a weekday "
      f"{max((b - a) / 3600 for a, b in zip(m1['time'][:-1], m1['time'][1:]) if datetime.fromtimestamp(int(a), UTC).weekday() < 4):.1f} h")

day1, day15 = m1["time"] // 86400, m15["time"] // 86400
bounds = {}
for d in np.unique(day1):
    lo, hi = np.searchsorted(day1, [d, d + 1])
    if hi - lo >= 60:
        bounds[int(d)] = (lo, hi)
sessions = sorted(bounds)
prof_cache = {}


def prev_profile(day):
    i = np.searchsorted(sessions, day) - 1
    if i < 0:
        return None
    p = sessions[i]
    if p not in prof_cache:
        lo, hi = bounds[p]
        prof_cache[p] = compute_profile(m1[lo:hi])
    return prof_cache[p]


def path(t0, entry, buy, rp, T=(1.0, 2.0)):
    i0 = np.searchsorted(m1["time"], t0)
    w = m1[i0:np.searchsorted(m1["time"], t0 + MAX_HOLD)]
    if len(w) < 2 or rp <= 0:
        return None
    sp = w["spread"] * PT
    fav = ((w["high"] - entry) if buy else (entry - (w["low"] + sp))) / rp
    adv = ((w["low"] - entry) if buy else (entry - (w["high"] + sp))) / rp
    hs = np.nonzero(adv <= -1)[0]
    i_s = hs[0] if len(hs) else 10 ** 9
    out = {}
    for t in T:
        ht = np.nonzero(fav >= t)[0]
        i_t = ht[0] if len(ht) else 10 ** 9
        if i_t < i_s:
            out[t], x = t, int(w["time"][i_t])
        elif i_s < 10 ** 9:
            out[t], x = -1.0, int(w["time"][i_s])
        else:
            if int(w["time"][0]) + MAX_HOLD - 120 > NOW.timestamp():
                return None
            c = w["close"][-1]
            out[t], x = float(((c - entry) if buy else (entry - c - sp[-1])) / rp), int(w["time"][-1])
        out[f"x{t}"] = x
    return out


rows = []
for day in np.unique(day15):
    if day * 86400 < START.timestamp():
        continue
    prof = prev_profile(int(day))
    if prof is None or not prof.closed_inside:
        continue
    lo, hi = np.searchsorted(day15, [day, day + 1])
    tb, ta = m15[lo:hi], atr[lo:hi]
    n_edge = {"buy": 0, "sell": 0}
    for k in range(2, len(tb) + 1):
        s = S.detect_va_reversal(tb[:k], ta[:k], prof)
        if s is None or s.blocked:
            continue
        n_edge[s.direction] += 1
        buy = s.direction == "buy"
        et = s.trigger_time + 900
        spr = float(tb[k - 1]["spread"]) * PT
        e_fill = s.entry + spr if buy else s.entry
        rp = (e_fill - s.sl) if buy else (s.sl - e_fill)
        res = path(et, e_fill, buy, rp)
        if res is None:
            continue
        mir = path(et, s.entry if buy else s.entry + spr, not buy, rp, T=(1.0,))
        a = float(s.detail["atr"])
        base = []
        cand = np.nonzero((m15["time"] >= et - 5 * 86400) & (m15["time"] <= et + 5 * 86400))[0]
        for j in rng.choice(cand, size=min(10, len(cand)), replace=False) if len(cand) else []:
            eb = float(m15["close"][j]) + (float(m15["spread"][j]) * PT if buy else 0)
            pb = path(int(m15["time"][j]) + 900, eb, buy, (rp / a) * atr[j], T=(1.0,))
            if pb:
                base.append(pb[1.0])
        rows.append(dict(dir=s.direction, t=et, entry=e_fill, sl=s.sl, risk=rp, r2=res[2.0], x2=res["x2.0"],
                         r1=res[1.0], mirror=mir[1.0] if mir else None, base=base, n_edge=n_edge[s.direction],
                         spread_r=spr / rp if rp > 0 else 9, forbidden=forbidden(et),
                         room=((prof.vah - e_fill) if buy else (e_fill - prof.val)) / rp if rp > 0 else 0,
                         hour=datetime.fromtimestamp(et, UTC).hour))
rows.sort(key=lambda r: r["t"])
json.dump(rows, open(OUT, "w"), default=float)
print(f"{len(rows)} resolved re-entry signals saved -> {OUT}")


def take(rs):
    busy, out = 0, []
    for r in rs:
        if busy > r["t"]:
            continue
        busy = r["x2"]
        out.append(r)
    return out


def st(rs):
    if not rs:
        return "n=   0"
    r = np.array([x["r2"] for x in rs])
    gp, gl = r[r > 0].sum(), -r[r <= 0].sum()
    eq = np.cumsum(np.r_[0.0, r])
    dd = float((eq - np.maximum.accumulate(eq)).min())
    sk = cur = 0
    for v in r:
        cur = cur + 1 if v < 0 else 0
        sk = max(sk, cur)
    boot = [rng.choice(r, len(r)).mean() for _ in range(2000)]
    return (f"n={len(r):4d} WR {100 * (r > 0).mean():3.0f}% avg {r.mean():+.3f}R "
            f"[90% CI {np.percentile(boot, 5):+.3f} .. {np.percentile(boot, 95):+.3f}] sum {r.sum():+6.1f}R "
            f"PF {gp / gl if gl else 9.9:4.2f} DD {dd:6.1f}R streak {sk:2d}")


ts = lambda y, m: datetime(y, m, 1, tzinfo=UTC).timestamp()
PER = [("Q4 2023", ts(2023, 10), ts(2024, 1)), ("2024 H1", ts(2024, 1), ts(2024, 7)), ("2024 H2", ts(2024, 7), ts(2025, 1)),
       ("2025 H1", ts(2025, 1), ts(2025, 7)), ("2025 H2", ts(2025, 7), ts(2026, 1)), ("2026 H1", ts(2026, 1), ts(2026, 7)),
       ("2026 Q3+", ts(2026, 7), ts(2027, 1))]
live = [r for r in rows if r["room"] >= 1.0 and not r["forbidden"]]
VAR = [("as live (room >= 1R)", lambda r: True), ("+ spread < 10%", lambda r: r["spread_r"] < 0.10),
       ("+ first per edge", lambda r: r["n_edge"] == 1),
       ("+ both filters", lambda r: r["spread_r"] < 0.10 and r["n_edge"] == 1)]
print("\n=== GOLD, LIVE-LIKE, 3 YEARS ===")
for lab, f in VAR:
    print(f"  {lab:22s} {st(take([r for r in live if f(r)]))}")
print("\n  sum R per period:" + " " * 6 + "".join(f"{n:>10s}" for n, _, _ in PER))
for lab, f in VAR:
    t = take([r for r in live if f(r)])
    cells = [sum(r["r2"] for r in t if a <= r["t"] < b) for _, a, b in PER]
    print(f"  {lab:22s}" + "".join(f"{c:+10.1f}" for c in cells) + f"   positive {sum(c > 0 for c in cells)}/{len(cells)}")
print("\n=== EDGE TEST (+1R before -1R, every re-entry signal) ===")
for lab, a, b in PER + [("ALL", 0, 9e12)]:
    x = [r for r in rows if a <= r["t"] < b]
    print(f"  {lab:9s} VP {100 * np.mean([r['r1'] > 0 for r in x]):5.1f}%   opposite "
          f"{100 * np.mean([r['mirror'] > 0 for r in x if r['mirror'] is not None]):5.1f}%   random "
          f"{100 * np.mean([v > 0 for r in x for v in r['base']]):5.1f}%   n={len(x)}")
print("\n=== HOUR OF ENTRY (live-like, as live), by half of the data ===")
L = take(live)
mid = L[len(L) // 2]["t"]
for h0 in range(0, 24, 6):
    g = [r for r in L if h0 <= r["hour"] < h0 + 6]
    h1 = sum(r["r2"] for r in g if r["t"] < mid)
    h2 = sum(r["r2"] for r in g if r["t"] >= mid)
    print(f"  {h0:02d}-{h0 + 6:02d} UTC  n={len(g):3d}  sum {sum(r['r2'] for r in g):+6.1f}R   halves {h1:+5.1f} / {h2:+5.1f}")
