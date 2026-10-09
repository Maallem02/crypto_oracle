"""WHY DO VOLUME PROFILE RE-ENTRIES FAIL?  Read-only.

Every va_reversal signal the PRODUCTION detector gives from 01/08 to now on
BTC ETH XAU USDJPY (live) + XAG (reference), M15 signals, previous-session
profile, entry at the trigger close. Per signal:
  * outcome at the live rule (stop / 2R, spread charged, stop first on ties,
    96 h max) and the +1R-before-1R test, the same moment in the opposite
    direction (mirror), 20 random times (same symbol, direction, stop in ATR)
  * the setup's own geometry: bars outside, depth of the excursion, stop size,
    value-area width, room to the far edge, distance to the POC, how far back
    inside the trigger closed
  * context, no look-ahead: H1 / H4 EMA50 slope vs the trade, 24 h move vs the
    trade, H1 ADX, where the session opened vs the value area, the previous
    day's direction, n-th signal of the session on that edge, hour, weekday
Then: edge or not, what separates winners from losers in BOTH halves, the
live-like trade set (1 position per symbol) under candidate filters."""
import json
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import MetaTrader5 as mt5
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import atr_series, detect

UTC = timezone.utc
OUT = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\SESSION_HANDOFF\scripts\vp_entry_rows.json"
SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAUUSDm", "USDJPYm", "XAGUSDm"]
START = datetime(2026, 8, 1, tzinfo=UTC)
M1_FROM = datetime(2026, 7, 29, 19, tzinfo=UTC)
MAX_HOLD = 96 * 3600
rng = np.random.default_rng(11)
mt5.initialize(timeout=10000)
NOW = datetime.now(UTC)


def ema_series(x, n):
    k, out = 2 / (n + 1), np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def adx_series(b, n=14):
    h, l, c = (np.asarray(b[k], float) for k in ("high", "low", "close"))
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    def wild(x):
        o = np.empty(len(x)); o[0] = x[:n].mean() if len(x) >= n else x[0]
        for i in range(1, len(x)):
            o[i] = (o[i - 1] * (n - 1) + x[i]) / n
        return o
    atr_, p_, n_ = wild(tr), wild(pdm), wild(ndm)
    pdi, ndi = 100 * p_ / np.maximum(atr_, 1e-12), 100 * n_ / np.maximum(atr_, 1e-12)
    dx = 100 * abs(pdi - ndi) / np.maximum(pdi + ndi, 1e-12)
    return np.concatenate(([np.nan], wild(dx)))


def path(m, t0, entry, buy, rp, point, T=(1.0, 2.0)):
    """first touch of +T before -1R from t0 on M1 array m (spread charged)."""
    i0 = np.searchsorted(m["time"], t0)
    w = m[i0:np.searchsorted(m["time"], t0 + MAX_HOLD)]
    if len(w) < 3 or rp <= 0:
        return None
    sp = w["spread"] * point
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
                return None                      # still open today: unresolved
            c = w["close"][-1]
            out[t], x = float(((c - entry) if buy else (entry - c - sp[-1])) / rp), int(w["time"][-1])
        out[f"x{t}"] = x
    return out


rows = []
for sym in SYMBOLS:
    si = mt5.symbol_info(sym)
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, M1_FROM, NOW)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, START - timedelta(days=40), NOW)
    h1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, START - timedelta(days=40), NOW)
    h4 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H4, START - timedelta(days=90), NOW)
    d1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, START - timedelta(days=30), NOW)
    m15 = m15[m15["time"] + 900 <= NOW.timestamp()]
    atr_all = atr_series(m15)
    e1, e4 = ema_series(h1["close"].astype(float), 50), ema_series(h4["close"].astype(float), 50)
    adx1 = adx_series(h1)
    m1_day, m15_day = m1["time"] // 86400, m15["time"] // 86400
    days_with = sorted(d for d in set(m1_day.tolist()) if (m1_day == d).sum() >= 60)
    for day in sorted(set(m15_day.tolist())):
        if day * 86400 < START.timestamp():
            continue
        prev = [d for d in days_with if d < day]
        if not prev:
            continue
        prof = compute_profile(m1[m1_day == prev[-1]])
        if prof is None or not prof.closed_inside:
            continue
        idx = np.where(m15_day == day)[0]
        tb, ta = m15[idx], atr_all[idx]
        seen = set()
        n_edge = {"buy": 0, "sell": 0}
        for k in range(2, len(tb) + 1):
            s = detect(tb[:k], ta[:k], prof, enabled={"poc_bounce": False, "va_breakout": False})
            if s is None or s.setup != "va_reversal" or s.trigger_time in seen:
                continue
            seen.add(s.trigger_time)
            if s.blocked:
                continue
            n_edge[s.direction] += 1
            buy = s.direction == "buy"
            et = s.trigger_time + 900
            a = float(s.detail["atr"])
            spr_e = float(tb[k - 1]["spread"]) * si.point
            e_fill = s.entry + spr_e if buy else s.entry
            rp = (e_fill - s.sl) if buy else (s.sl - e_fill)
            res = path(m1, et, e_fill, buy, rp, si.point)
            if res is None:
                continue
            mir_e = s.entry if buy else s.entry + spr_e        # opposite side pays the spread too
            mir = path(m1, et, mir_e, not buy, rp, si.point, T=(1.0,))
            # random baseline: same symbol, same direction, same stop in ATR, +-5 days
            base = []
            lo_t, hi_t = max(et - 5 * 86400, START.timestamp()), min(et + 5 * 86400, NOW.timestamp() - MAX_HOLD)
            cand = np.nonzero((m15["time"] >= lo_t) & (m15["time"] <= hi_t))[0]
            for j in rng.choice(cand, size=min(20, len(cand)), replace=False) if len(cand) else []:
                rb = s.detail["risk_atr"] * atr_all[j]
                eb = float(m15["close"][j]) + (float(m15["spread"][j]) * si.point if buy else 0)
                pb = path(m1, int(m15["time"][j]) + 900, eb, buy, rb, si.point, T=(1.0,))
                if pb:
                    base.append(pb[1.0])
            # context (closed bars only)
            sgn = 1 if buy else -1
            j1 = np.searchsorted(h1["time"] + 3600, et, side="right") - 1
            j4 = np.searchsorted(h4["time"] + 14400, et, side="right") - 1
            jd = np.searchsorted(d1["time"] + 86400, et, side="right") - 1
            ctx = {}
            if j1 >= 6:
                ctx["h1_slope"] = "with" if (e1[j1] > e1[j1 - 5]) == buy else "against"
                ctx["h1_side"] = "with" if (h1["close"][j1] > e1[j1]) == buy else "against"
                ctx["adx_h1"] = float(adx1[j1])
                ctx["mom24"] = sgn * float(h1["close"][j1] - h1["close"][max(0, j1 - 24)]) / a
            if j4 >= 6:
                ctx["h4_slope"] = "with" if (e4[j4] > e4[j4 - 3]) == buy else "against"
            if jd >= 0:
                ctx["prevday"] = "with" if (d1["close"][jd] > d1["open"][jd]) == buy else "against"
            o0 = float(tb[0]["open"])
            ctx["open_vs_va"] = "inside" if prof.val <= o0 <= prof.vah else (
                "beyond the trade's edge" if (o0 < prof.val) == buy else "beyond the far edge")
            swing = s.detail["swing"]
            edge = prof.val if buy else prof.vah
            far = prof.vah if buy else prof.val
            n_out = int(s.reason.split("after ")[1].split(" bar")[0])
            rows.append(dict(
                sym=sym[:-1], dir=s.direction, t=et, entry=e_fill, sl=s.sl, risk=rp,
                r2=res[2.0], x2=res["x2.0"], r1=res[1.0], mirror=mir[1.0] if mir else None, base=base,
                n_out=n_out, depth=sgn * (edge - swing) / a, risk_atr=rp / a,
                va_w=(prof.vah - prof.val) / a, room=sgn * (far - e_fill) / rp,
                to_poc=sgn * (prof.poc - e_fill) / rp, back_in=sgn * (s.entry - edge) / a,
                n_edge=n_edge[s.direction], spread_r=spr_e / rp,
                hour=datetime.fromtimestamp(et, UTC).hour, wd=datetime.fromtimestamp(et, UTC).weekday(),
                ctx=ctx))
    print(f"{sym}: {sum(1 for r in rows if r['sym'] == sym[:-1])} signals")
mt5.shutdown()
json.dump(rows, open(OUT, "w"), default=float)
print(f"{len(rows)} resolved re-entry signals saved")
