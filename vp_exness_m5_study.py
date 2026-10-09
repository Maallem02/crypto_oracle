"""VP re-entry on GOLD and USDJPY over ~16 months of the broker's own data
(Exness, MT5). Read-only.

MT5 serves at most "Max bars in chart" (100,000) bars per timeframe, so 1-minute
history only reaches July 2026. 5-minute history reaches May/June 2025. This
study therefore builds the previous-session profile from M5 bars (the bot uses
M1) and resolves stops/targets on M5 (stop first when both are in one bar).

PART A — fidelity, July -> October 2026, where M1 exists: the same days run
  with M1 profiles + M1 paths (the bot's method) and with M5 profiles + M5
  paths. Level gaps, signal agreement and live-like totals are compared.
PART B — every day from June 2025, M5 method, live rules: production detector
  (va_reversal), previous session = last UTC day with >= 1 hour of quotes,
  entry at the trigger close + that bar's spread, stop / 2R, 96 h max, the
  executor's forbidden windows for forex/metals (Monday < 02:00, 23:45-00:30),
  room >= 1R, 1 position per symbol. Edge test vs the opposite side and 10
  random entries. Not modelled: news pause / news close (no history of the
  calendar), rollover spread spikes beyond the bar spread."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import MetaTrader5 as mt5
from features.volume_profile import strategy as S
from features.volume_profile.profile import compute_profile

UTC = timezone.utc
SYMS = ["XAUUSDm", "USDJPYm"]
MAX_HOLD = 96 * 3600
OUT = os.path.join(HERE, "data", "vp_exness_m5_rows.json")
rng = np.random.default_rng(11)


def forbidden(t):
    d = datetime.fromtimestamp(t, UTC)
    return (d.weekday() == 0 and d.hour < 2) or (d.hour == 23 and d.minute >= 45) or (d.hour == 0 and d.minute < 30)


class Src:
    """One symbol, one 'fine' timeframe (M1 or M5) for profiles and paths."""
    def __init__(self, sym, fine, fine_sec, m15, now_ts):
        self.sym, self.f, self.fsec, self.now = sym, fine, fine_sec, now_ts
        self.pt = mt5.symbol_info(sym).point
        self.m15, self.atr = m15, S.atr_series(m15)
        self.fday, self.day15 = fine["time"] // 86400, m15["time"] // 86400
        self.min_bars = 3600 // fine_sec                      # the bot: >= 60 M1 bars = 1 hour of quotes
        self.days = sorted(d for d in set(self.fday.tolist()) if (self.fday == d).sum() >= self.min_bars)
        self._prof = {}

    def prev_profile(self, day):
        prev = [d for d in self.days if d < day][-1:] if self.days and self.days[0] < day else []
        if not prev:
            return None, None
        p = prev[0]
        if p not in self._prof:
            self._prof[p] = compute_profile(self.f[self.fday == p])
        return p, self._prof[p]

    def path(self, t0, entry, buy, rp, T=(1.0, 2.0)):
        i0 = np.searchsorted(self.f["time"], t0)
        w = self.f[i0:np.searchsorted(self.f["time"], t0 + MAX_HOLD)]
        if len(w) < 2 or rp <= 0:
            return None
        sp = w["spread"] * self.pt
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
                if int(w["time"][0]) + MAX_HOLD - 2 * self.fsec > self.now:
                    return None                                  # still open: unresolved
                c = w["close"][-1]
                out[t], x = float(((c - entry) if buy else (entry - c - sp[-1])) / rp), int(w["time"][-1])
            out[f"x{t}"] = x
        return out

    def signals(self, day, full=True):
        p, prof = self.prev_profile(day)
        if prof is None or not prof.closed_inside:
            return []
        lo, hi = np.searchsorted(self.day15, [day, day + 1])
        tb, ta = self.m15[lo:hi], self.atr[lo:hi]
        rows, n_edge = [], {"buy": 0, "sell": 0}
        for k in range(2, len(tb) + 1):
            s = S.detect_va_reversal(tb[:k], ta[:k], prof)
            if s is None or s.blocked:
                continue
            n_edge[s.direction] += 1
            buy = s.direction == "buy"
            et = s.trigger_time + 900
            spr = float(tb[k - 1]["spread"]) * self.pt
            e_fill = s.entry + spr if buy else s.entry
            rp = (e_fill - s.sl) if buy else (s.sl - e_fill)
            row = dict(sym=self.sym[:-1], dir=s.direction, t=et, entry=e_fill, sl=s.sl, risk=rp,
                       n_edge=n_edge[s.direction], spread_r=spr / rp if rp > 0 else 9,
                       room=((prof.vah - e_fill) if buy else (e_fill - prof.val)) / rp if rp > 0 else 0,
                       forbidden=forbidden(et), prof_day=int(p))
            if not full:
                rows.append(row)
                continue
            res = self.path(et, e_fill, buy, rp)
            if res is None:
                continue
            row.update(r2=res[2.0], x2=res["x2.0"], r1=res[1.0])
            mir = self.path(et, s.entry if buy else s.entry + spr, not buy, rp, T=(1.0,))
            row["mirror"] = mir[1.0] if mir else None
            base, a = [], float(s.detail["atr"])
            cand = np.nonzero((self.m15["time"] >= et - 5 * 86400) & (self.m15["time"] <= et + 5 * 86400))[0]
            for j in rng.choice(cand, size=min(10, len(cand)), replace=False) if len(cand) else []:
                rb = (rp / a) * self.atr[j]
                eb = float(self.m15["close"][j]) + (float(self.m15["spread"][j]) * self.pt if buy else 0)
                pb = self.path(int(self.m15["time"][j]) + 900, eb, buy, rb, T=(1.0,))
                if pb:
                    base.append(pb[1.0])
            row["base"] = base
            rows.append(row)
        return rows


def take(rows):
    busy, out = {}, []
    for r in sorted(rows, key=lambda r: r["t"]):
        if busy.get(r["sym"], 0) > r["t"]:
            continue
        busy[r["sym"]] = r["x2"]
        out.append(r)
    return out


def st(rows):
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
    return (f"n={len(r):4d} WR {100 * (r > 0).mean():3.0f}% avg {r.mean():+.3f} sum {r.sum():+6.1f}R "
            f"PF {gp / gl if gl else 9.9:4.2f} DD {dd:6.1f}R streak {sk:2d}")


live_ok = lambda r: r["room"] >= 1.0 and not r["forbidden"]

mt5.initialize(timeout=10000)
NOW = datetime.now(UTC)
data = {}
for sym in SYMS:
    m1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 99000)
    m5 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 99000)
    m15 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 99000)
    m15 = m15[m15["time"] + 900 <= NOW.timestamp()]
    data[sym] = (Src(sym, m1, 60, m15, NOW.timestamp()), Src(sym, m5, 300, m15, NOW.timestamp()))
    print(f"{sym}: M1 from {datetime.fromtimestamp(int(m1['time'][0]), UTC):%d/%m/%Y}, "
          f"M5 from {datetime.fromtimestamp(int(m5['time'][0]), UTC):%d/%m/%Y}, "
          f"M15 from {datetime.fromtimestamp(int(m15['time'][0]), UTC):%d/%m/%Y}")

print("\n" + "=" * 100 + "\nPART A - fidelity: M1 method (the bot) vs M5 method, same days\n" + "=" * 100)
for sym in SYMS:
    s1, s5 = data[sym]
    days = [d for d in sorted(set(s1.day15.tolist())) if d > s1.days[1]]
    gaps, verdict, a_rows1, a_rows5 = [], [], [], []
    for day in days:
        p1, pr1 = s1.prev_profile(day)
        p5, pr5 = s5.prev_profile(day)
        if pr1 is None or pr5 is None:
            continue
        lo = np.searchsorted(s1.day15, day)
        a = float(s1.atr[lo])
        gaps.append([abs(pr1.poc - pr5.poc) / a, abs(pr1.vah - pr5.vah) / a, abs(pr1.val - pr5.val) / a])
        verdict.append(pr1.closed_inside == pr5.closed_inside)
        a_rows1 += s1.signals(day)
        a_rows5 += s5.signals(day)
    g = np.array(gaps)
    m = sum(1 for r in a_rows1 if any(q["dir"] == r["dir"] and abs(q["t"] - r["t"]) <= 1800 for q in a_rows5))
    print(f"  {sym[:-1]}: {len(days)} days | level gap median ATR15 POC {np.median(g[:, 0]):.2f} VAH {np.median(g[:, 1]):.2f} "
          f"VAL {np.median(g[:, 2]):.2f} | same inside/outside {100 * np.mean(verdict):.0f}% | "
          f"M1 signals {len(a_rows1)}, found by M5 {100 * m / max(len(a_rows1), 1):.0f}%")
    print(f"     live-like, M1 method: {st(take([r for r in a_rows1 if live_ok(r)]))}")
    print(f"     live-like, M5 method: {st(take([r for r in a_rows5 if live_ok(r)]))}")

print("\n" + "=" * 100 + "\nPART B - every day from June 2025, M5 method\n" + "=" * 100)
rows = []
for sym in SYMS:
    s5 = data[sym][1]
    for day in sorted(set(s5.day15.tolist())):
        if day > s5.days[0]:
            rows += s5.signals(day)
    print(f"  {sym[:-1]}: {sum(1 for r in rows if r['sym'] == sym[:-1])} resolved re-entry signals")
mt5.shutdown()
json.dump(rows, open(OUT, "w"), default=float)
rows.sort(key=lambda r: r["t"])
ts = lambda y, m: datetime(y, m, 1, tzinfo=UTC).timestamp()
PER = [("Jun-Sep 25", ts(2025, 6), ts(2025, 10)), ("Q4 2025", ts(2025, 10), ts(2026, 1)),
       ("Q1 2026", ts(2026, 1), ts(2026, 4)), ("Q2 2026", ts(2026, 4), ts(2026, 7)),
       ("Q3+ 2026", ts(2026, 7), ts(2027, 1))]
first = lambda r: r["n_edge"] == 1
spread_ok = lambda r: r["spread_r"] < 0.10
VAR = [("as live (room >= 1R)", lambda r: True), ("+ spread < 10%", spread_ok), ("+ first per edge", first),
       ("+ both filters", lambda r: spread_ok(r) and first(r))]
base = [r for r in rows if live_ok(r)]
for who in (("XAUUSD", "USDJPY"), ("XAUUSD",), ("USDJPY",)):
    print(f"\n  {' + '.join(who)}" + " " * 18 + "".join(f"{n:>11s}" for n, _, _ in PER))
    for lab, f in VAR:
        t = take([r for r in base if f(r) and r["sym"] in who])
        cells = "".join(f"{sum(r['r2'] for r in t if a <= r['t'] < b):+11.1f}" for _, a, b in PER)
        print(f"    {lab:22s}{cells}   | {st(t)}")

print("\n  EDGE TEST (+1R before -1R, every re-entry signal)")
for who in ("XAUUSD", "USDJPY"):
    rr = [r for r in rows if r["sym"] == who]
    for lab, a, b in PER + [("ALL", 0, 9e12)]:
        x = [r for r in rr if a <= r["t"] < b]
        if len(x) < 10:
            continue
        print(f"    {who} {lab:10s} VP {100 * np.mean([r['r1'] > 0 for r in x]):5.1f}%   opposite "
              f"{100 * np.mean([r['mirror'] > 0 for r in x if r['mirror'] is not None]):5.1f}%   random "
              f"{100 * np.mean([v > 0 for r in x for v in r['base']]):5.1f}%   n={len(x)}")
