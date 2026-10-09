"""What separates VP re-entry winners from losers? Features known AT ENTRY only.
Re-entry setup, 1 VP trade per symbol, 2R, 5 live symbols, ~70 days of M1.
Method guarding against curve-fitting: every bucket is shown for the 1st half
and the 2nd half separately; a filter is only credible if it helps in BOTH,
and the final candidate is chosen on the 1st half and judged on the 2nd."""
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import MetaTrader5 as mt5
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import atr_series, detect

UTC = timezone.utc
SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAGUSDm", "XAUUSDm", "USDJPYm"]
MAX_HOLD = 72 * 3600
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
first_m1 = None
rows = []
for sym in SYMBOLS:
    si = mt5.symbol_info(sym)
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, now - timedelta(days=66), now)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, now - timedelta(days=72), now)
    m15 = m15[m15["time"] + 900 <= now.timestamp()]
    atr_all = atr_series(m15)
    m1_day, m15_day = m1["time"] // 86400, m15["time"] // 86400
    days_with = sorted(d for d in set(m1_day.tolist()) if (m1_day == d).sum() >= 60)
    start_day = days_with[1]
    for day in sorted(set(m15_day.tolist())):
        if day < start_day:
            continue
        prev = [d for d in days_with if d < day]
        prof = compute_profile(m1[m1_day == prev[-1]])
        if prof is None:
            continue
        idx = np.where(m15_day == day)[0]
        tb, ta = m15[idx], atr_all[idx]
        for k in range(2, len(tb) + 1):
            s = detect(tb[:k], ta[:k], prof, enabled={"poc_bounce": False, "va_breakout": False})
            if s is None or s.blocked:
                continue
            et = s.trigger_time + 900
            fwd = m1[np.searchsorted(m1["time"], et):np.searchsorted(m1["time"], et + MAX_HOLD)]
            if len(fwd) == 0:
                continue
            buy = s.direction == "buy"
            sgn = 1 if buy else -1
            r, xt = None, None
            for b in fwd:
                if (b["low"] <= s.sl) if buy else (b["high"] >= s.sl):
                    r, xt = -1.0, int(b["time"]); break
                if (b["high"] >= s.tp) if buy else (b["low"] <= s.tp):
                    r, xt = 2.0, int(b["time"]); break
            if r is None:
                if int(fwd[0]["time"]) + MAX_HOLD - 60 > now.timestamp():
                    continue
                r, xt = sgn * (float(fwd[-1]["close"]) - s.entry) / s.risk, int(fwd[-1]["time"])
            a = float(ta[k - 1])
            bar = tb[k - 1]
            gi = idx[k - 1]                                  # index in the full M15 array
            rng = float(bar["high"] - bar["low"]) or 1e-9
            vol20 = float(np.mean(m15["tick_volume"][max(0, gi - 20):gi])) or 1.0
            mom24 = (s.entry - float(m15["close"][max(0, gi - 96)])) / a       # 24h move, ATR
            n_out = int(re.search(r"after (\d+) bar", s.reason).group(1))
            depth = ((prof.val - s.detail["swing"]) if buy else (s.detail["swing"] - prof.vah)) / a
            rows.append(dict(
                sym=sym[:-1], t=et, r=r - float(bar["spread"]) * si.point / s.risk, x=xt, dir=s.direction,
                n_out=n_out, depth=depth, risk_atr=s.risk / a,
                va_w=(prof.vah - prof.val) / a,
                room=((prof.vah - s.entry) if buy else (s.entry - prof.val)) / s.risk,   # to the far VA edge, in R
                poc_r=((prof.poc - s.entry) if buy else (s.entry - prof.poc)) / s.risk,
                body=sgn * float(bar["close"] - bar["open"]) / rng,                    # reclaim candle strength
                vol=float(bar["tick_volume"]) / vol20,
                trend24=sgn * mom24,                                                   # >0 = with the 24h move
                day=sgn * (s.entry - float(tb[0]["open"])) / a,                        # >0 = with today's move
                hour=datetime.fromtimestamp(et, UTC).hour,
                wd=datetime.fromtimestamp(et, UTC).weekday()))
mt5.shutdown()

# one VP trade per symbol (the live rule)
rows.sort(key=lambda x: x["t"])
busy, T = {}, []
for x in rows:
    if x["t"] < busy.get(x["sym"], 0):
        continue
    busy[x["sym"]] = x["x"]
    T.append(x)
MID = np.median([x["t"] for x in T])
H1 = [x for x in T if x["t"] < MID]
H2 = [x for x in T if x["t"] >= MID]
print(f"{len(T)} re-entry trades, {datetime.fromtimestamp(T[0]['t'], UTC):%d/%m} -> {datetime.fromtimestamp(T[-1]['t'], UTC):%d/%m}, "
      f"halves split {datetime.fromtimestamp(MID, UTC):%d/%m}")


def s(rs):
    if not rs:
        return "   n=0                 "
    r = np.array([x["r"] for x in rs])
    return f"n={len(r):3d} WR={100 * (r > 0).mean():3.0f}% {r.sum():+6.1f}R ({r.mean():+.2f}/tr)"


def combo(name, keep):
    # re-apply the 1-per-symbol rule on the filtered signal list
    busy2, sel = {}, []
    for x in rows:
        if not keep(x) or x["t"] < busy2.get(x["sym"], 0):
            continue
        busy2[x["sym"]] = x["x"]
        sel.append(x)
    r = np.array([x["r"] for x in sel])
    eq = np.cumsum(r)
    dd = (np.maximum.accumulate(np.concatenate(([0], eq)))[1:] - eq).max()
    h1 = sum(x["r"] for x in sel if x["t"] < MID)
    h2 = sum(x["r"] for x in sel if x["t"] >= MID)
    print(f"  {name:44s} n={len(r):3d} WR={100 * (r > 0).mean():3.0f}% total={r.sum():+6.1f}R  "
          f"avg={r.mean():+.2f}R/trade  maxDD={dd:4.1f}R  halves {h1:+5.1f} / {h2:+5.1f}")

print("CANDIDATE CHANGES (same signals, 1 VP trade per symbol re-applied):")
combo("current: 5 symbols, all re-entry signals", lambda x: True)
combo("drop silver", lambda x: x["sym"] != "XAGUSD")
combo("drop silver + ETH", lambda x: x["sym"] not in ("XAGUSD", "ETHUSD"))
combo("skip when far VA edge < 1R away", lambda x: x["room"] >= 1)
combo("drop silver + skip far edge < 1R", lambda x: x["sym"] != "XAGUSD" and x["room"] >= 1)
