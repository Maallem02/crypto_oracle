"""VP LONG-HISTORY STUDY (step 1) — does the Volume Profile re-entry edge hold
over 3 years, or did we see 10 lucky weeks?  Read-only (MT5 only read for the
validation stage).

Data: Binance spot 1-minute klines, BTCUSDT + ETHUSDT, 01/10/2023 -> 07/10/2026
(data/binance_1m/*.zip, verified against Binance's sha256 checksums).

STAGE A — validation on the overlap with the live bot (01/08 -> 07/10/2026).
  Profiles rebuilt from Binance with two volume weightings (traded base volume,
  trade count) and compared with the Exness profiles the bot computes (MT5 M1
  tick volume): level differences in ATR, and re-entry signals matched against
  the Exness replay rows (vp_trend_rows.json) within 30 min, same direction.
  The weighting that reproduces the Exness signals best is used for stage B.

STAGE B — every UTC day 02/10/2023 -> 07/10/2026, the PRODUCTION detector
  (features/volume_profile/strategy.py, each setup run on its own), previous
  session's profile (production compute_profile), entry at the trigger close.
  Outcome as live: stop / 2R, stop first on ties, 96 h max, Exness-like spread
  paid (BTC 0.0119 % of price = $10 at 84k, ETH 0.037 % = $1 at 2.7k).
  Per signal also: +1R-before-1R, the same moment in the opposite direction,
  10 random times (same symbol, direction, stop in ATR, +-5 days), geometry,
  trend context and Binance-only order flow (taker buy share). Rows are saved
  to data/binance_1m/vp_long_rows.json for the analysis script."""
import glob
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from features.volume_profile import strategy as S
from features.volume_profile.profile import compute_profile

UTC = timezone.utc
DATA = os.path.join(HERE, "data", "binance_1m")
OUT = os.path.join(DATA, "vp_long_rows.json")
SPREAD = {"BTCUSD": 10.0 / 84000, "ETHUSD": 1.0 / 2700}      # fraction of price
PAIRS = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
MAX_HOLD = 96 * 3600
# Exness replay of the same detector (vp_trend_study.py, 08/10/2026): the signals the bot's data gives
EX_ROWS = (r"C:\Users\MSI\AppData\Local\Temp\claude\C--Users-MSI-Desktop-loumi-crypt"
           r"\b8590a57-fc16-47b4-84f6-bc4648c42317\scratchpad\vp_trend_rows.json")
OVERLAP = (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 10, 8, tzinfo=UTC))
rng = np.random.default_rng(7)
M1_DT = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                  ("tick_volume", "f8"), ("trades", "f8"), ("taker", "f8"), ("vol", "f8")])


def load(pair):
    frames = []
    for f in sorted(glob.glob(os.path.join(DATA, f"{pair}-1m-*.zip"))):
        with zipfile.ZipFile(f) as z:
            frames.append(pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 1, 2, 3, 4, 5, 8, 9]))
    df = pd.concat(frames, ignore_index=True)
    t = df[0].to_numpy(np.int64)
    t = np.where(t > 10 ** 14, t // 1_000_000, t // 1000)        # microseconds from 2025, ms before
    order = np.argsort(t, kind="stable")
    t, keep = t[order], np.ones(len(t), bool)
    keep[1:] = t[1:] != t[:-1]
    a = np.zeros(int(keep.sum()), dtype=M1_DT)
    a["time"] = t[keep]
    for k, c in (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("vol", 5), ("trades", 8), ("taker", 9)):
        a[k] = df[c].to_numpy(float)[order][keep]
    return a


def resample(m1, sec):
    g = m1["time"] // sec
    starts = np.r_[0, np.nonzero(np.diff(g))[0] + 1]
    out = np.zeros(len(starts), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                                       ("close", "f8"), ("vol", "f8"), ("taker", "f8"), ("spread", "i4")])
    out["time"] = g[starts] * sec
    out["open"] = m1["open"][starts]
    out["close"] = m1["close"][np.r_[starts[1:] - 1, len(m1) - 1]]
    out["high"] = np.maximum.reduceat(m1["high"], starts)
    out["low"] = np.minimum.reduceat(m1["low"], starts)
    out["vol"] = np.add.reduceat(m1["vol"], starts)
    out["taker"] = np.add.reduceat(m1["taker"], starts)
    return out


def ema(x, n):
    k, o = 2 / (n + 1), np.empty(len(x))
    o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = x[i] * k + o[i - 1] * (1 - k)
    return o


class Sym:
    def __init__(self, name):
        self.name = name
        self.m1 = load(PAIRS[name])
        self.m15 = resample(self.m1, 900)
        self.m15 = self.m15[self.m15["time"] + 900 <= self.m1["time"][-1] + 60]
        self.atr = S.atr_series(self.m15)
        self.h1, self.h4, self.d1 = resample(self.m1, 3600), resample(self.m1, 14400), resample(self.m1, 86400)
        self.e1, self.e4 = ema(self.h1["close"], 50), ema(self.h4["close"], 50)
        self.e20d, self.atrd = ema(self.d1["close"], 20), S.atr_series(self.d1)
        self.day1 = self.m1["time"] // 86400
        self.day15 = self.m15["time"] // 86400
        self.sp = SPREAD[name]

    def day_bars(self, day):
        lo, hi = np.searchsorted(self.day1, [day, day + 1])
        return self.m1[lo:hi]

    def profile(self, day, weight="vol"):
        b = self.day_bars(day)
        if len(b) < 60:
            return None
        if weight != "tick_volume":
            b = b.copy()
            b["tick_volume"] = b[weight]
        return compute_profile(b)

    def path(self, t0, entry, buy, rp, T=(1.0, 2.0), now=None):
        i0 = np.searchsorted(self.m1["time"], t0)
        w = self.m1[i0:np.searchsorted(self.m1["time"], t0 + MAX_HOLD)]
        if len(w) < 3 or rp <= 0:
            return None
        sp = entry * self.sp
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
                if int(w["time"][0]) + MAX_HOLD - 120 > self.m1["time"][-1]:
                    return None                               # still open at the end of the data
                c = w["close"][-1]
                out[t], x = float(((c - entry) if buy else (entry - c - sp)) / rp), int(w["time"][-1])
            out[f"x{t}"] = x
        return out


def signals(sym, day, prof, setups=("va_reversal", "va_breakout", "poc_bounce"), full=True):
    lo, hi = np.searchsorted(sym.day15, [day, day + 1])
    tb, ta = sym.m15[lo:hi], sym.atr[lo:hi]
    rows, seen = [], set()
    n_edge = {"buy": 0, "sell": 0}
    fns = {"va_reversal": S.detect_va_reversal, "va_breakout": S.detect_va_breakout,
           "poc_bounce": S.detect_poc_bounce}
    for k in range(2, len(tb) + 1):
        for setup in setups:
            s = fns[setup](tb[:k], ta[:k], prof)
            if s is None or (setup, s.trigger_time, s.direction) in seen:
                continue
            seen.add((setup, s.trigger_time, s.direction))
            if s.blocked:
                continue
            buy = s.direction == "buy"
            if setup == "va_reversal":
                n_edge[s.direction] += 1
            et = s.trigger_time + 900
            a = float(s.detail["atr"])
            spr = float(tb[k - 1]["close"]) * sym.sp
            e_fill = s.entry + spr if buy else s.entry
            rp = (e_fill - s.sl) if buy else (s.sl - e_fill)
            row = dict(sym=sym.name, setup=setup, dir=s.direction, t=et, entry=e_fill, sl=s.sl, risk=rp,
                       n_edge=n_edge[s.direction] if setup == "va_reversal" else None, spread_r=spr / rp if rp > 0 else None)
            if setup == "va_reversal":
                row["room"] = ((prof.vah - e_fill) if buy else (e_fill - prof.val)) / rp if rp > 0 else 0
            if not full:
                rows.append(row)
                continue
            res = sym.path(et, e_fill, buy, rp)
            if res is None:
                continue
            row.update(r2=res[2.0], x2=res["x2.0"], r1=res[1.0])
            mir = sym.path(et, s.entry if buy else s.entry + spr, not buy, rp, T=(1.0,))
            row["mirror"] = mir[1.0] if mir else None
            base = []
            cand = np.nonzero((sym.m15["time"] >= et - 5 * 86400) & (sym.m15["time"] <= et + 5 * 86400))[0]
            for j in rng.choice(cand, size=min(10, len(cand)), replace=False) if len(cand) else []:
                rb = (rp / a) * sym.atr[j]
                eb = float(sym.m15["close"][j]) * (1 + (sym.sp if buy else 0))
                pb = sym.path(int(sym.m15["time"][j]) + 900, eb, buy, rb, T=(1.0,))
                if pb:
                    base.append(pb[1.0])
            row["base"] = base
            # geometry + context (closed bars only) + Binance order flow
            sgn = 1 if buy else -1
            ctx = dict(risk_atr=rp / a, va_w=(prof.vah - prof.val) / a, to_poc=sgn * (prof.poc - e_fill) / rp,
                       hour=datetime.fromtimestamp(et, UTC).hour, wd=datetime.fromtimestamp(et, UTC).weekday())
            if setup == "va_reversal":
                n_out = int(s.reason.split("after ")[1].split(" bar")[0])
                ex = tb[k - 1 - n_out:k - 1]
                ctx.update(n_out=n_out, depth=sgn * ((prof.val if buy else prof.vah) - s.detail["swing"]) / a,
                           ex_taker=float(ex["taker"].sum() / max(ex["vol"].sum(), 1e-12)),
                           ex_relvol=float(ex["vol"].mean() / max(tb[:k]["vol"].mean(), 1e-12)))
            trg = tb[k - 1]
            ctx["trg_taker"] = float(trg["taker"] / max(trg["vol"], 1e-12))
            j1 = np.searchsorted(sym.h1["time"] + 3600, et, side="right") - 1
            j4 = np.searchsorted(sym.h4["time"] + 14400, et, side="right") - 1
            jd = np.searchsorted(sym.d1["time"] + 86400, et, side="right") - 1
            if j1 >= 24:
                ctx["h1_slope"] = 1 if (sym.e1[j1] > sym.e1[j1 - 5]) == buy else -1
                ctx["mom24"] = sgn * float(sym.h1["close"][j1] - sym.h1["close"][j1 - 24]) / a
            if j4 >= 6:
                ctx["h4_slope"] = 1 if (sym.e4[j4] > sym.e4[j4 - 3]) == buy else -1
            if jd >= 25:
                ctx["d1_slope"] = 1 if (sym.e20d[jd] > sym.e20d[jd - 3]) == buy else -1
                ctx["mom3d"] = sgn * float(sym.d1["close"][jd] - sym.d1["close"][jd - 3]) / float(sym.atrd[jd])
            row["ctx"] = ctx
            rows.append(row)
    return rows


def stage_a(syms):
    import MetaTrader5 as mt5
    print("=" * 100)
    print("STAGE A - Binance vs Exness on the overlap 01/08 -> 07/10/2026")
    print("=" * 100)
    ex_rows = json.load(open(EX_ROWS))
    mt5.initialize(timeout=10000)
    best, best_f1 = "vol", -1
    for weight in ("vol", "trades"):
        lv, got, f1s = [], {}, []
        for sym in syms:
            em1 = mt5.copy_rates_range(sym.name + "m", mt5.TIMEFRAME_M1, OVERLAP[0] - timedelta(days=1), OVERLAP[1])
            eday = em1["time"] // 86400
            bsig = []
            for day in range(int(OVERLAP[0].timestamp() // 86400), int(OVERLAP[1].timestamp() // 86400)):
                eb = em1[eday == day - 1]
                pe, pb = (compute_profile(eb) if len(eb) >= 60 else None), sym.profile(day - 1, weight)
                if pe is None or pb is None:
                    continue
                lo = np.searchsorted(sym.day15, day)
                a = float(sym.atr[lo]) if lo < len(sym.atr) else 0
                if a > 0:
                    lv.append([abs(pe.poc - pb.poc) / a, abs(pe.vah - pb.vah) / a, abs(pe.val - pb.val) / a,
                               pe.closed_inside == pb.closed_inside])
                if pb.closed_inside:
                    bsig += signals(sym, day, pb, setups=("va_reversal",), full=False)
            ex = [r for r in ex_rows if r["sym"] == sym.name and OVERLAP[0].timestamp() <= r["t"] < OVERLAP[1].timestamp()]
            m_ex = sum(1 for r in ex if any(b["dir"] == r["dir"] and abs(b["t"] - r["t"]) <= 1800 for b in bsig))
            m_bn = sum(1 for b in bsig if any(b["dir"] == r["dir"] and abs(b["t"] - r["t"]) <= 1800 for r in ex))
            rec, prec = m_ex / max(len(ex), 1), m_bn / max(len(bsig), 1)
            f1 = 2 * rec * prec / max(rec + prec, 1e-9)
            f1s.append(f1)
            got[sym.name] = (len(ex), len(bsig), rec, prec)
        lv = np.array(lv)
        print(f"\n  weighting = {weight}")
        print(f"    level distance in ATR15 (median / 90th pct): POC {np.median(lv[:, 0]):.2f} / {np.percentile(lv[:, 0], 90):.2f}"
              f"   VAH {np.median(lv[:, 1]):.2f} / {np.percentile(lv[:, 1], 90):.2f}"
              f"   VAL {np.median(lv[:, 2]):.2f} / {np.percentile(lv[:, 2], 90):.2f}"
              f"   same inside/outside verdict {100 * lv[:, 3].mean():.0f}%")
        for k, (ne, nb, rec, prec) in got.items():
            print(f"    {k}: Exness signals {ne}, Binance signals {nb} | Exness found by Binance {100 * rec:.0f}% "
                  f"| Binance found in Exness {100 * prec:.0f}%")
        if np.mean(f1s) > best_f1:
            best, best_f1 = weight, float(np.mean(f1s))
    mt5.shutdown()
    print(f"\n  -> stage B uses weighting '{best}' (mean F1 {best_f1:.2f})")
    return best


def stage_b(syms, weight):
    print("\n" + "=" * 100)
    print(f"STAGE B - every day 02/10/2023 -> 07/10/2026, weighting '{weight}'")
    print("=" * 100)
    rows = []
    for sym in syms:
        days = np.unique(sym.day15)
        n0 = len(rows)
        for day in days[1:]:
            prof = sym.profile(day - 1, weight)
            if prof is None:
                continue
            setups = ("va_reversal", "va_breakout") if prof.closed_inside else ("va_breakout", "poc_bounce")
            rows += signals(sym, day, prof, setups=setups)
        print(f"  {sym.name}: {len(rows) - n0} resolved signals "
              f"({sum(1 for r in rows[n0:] if r['setup'] == 'va_reversal')} re-entries)")
    json.dump(dict(weight=weight, rows=rows), open(OUT, "w"), default=float)
    print(f"  saved {len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    syms = [Sym("BTCUSD"), Sym("ETHUSD")]
    for s in syms:
        print(f"{s.name}: {len(s.m1):,} 1-min bars {datetime.fromtimestamp(int(s.m1['time'][0]), UTC):%d/%m/%Y} -> "
              f"{datetime.fromtimestamp(int(s.m1['time'][-1]), UTC):%d/%m/%Y %H:%M} "
              f"(gaps > 5 min: {int((np.diff(s.m1['time']) > 300).sum())})")
    w = stage_a(syms)
    stage_b(syms, w)
