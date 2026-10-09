"""How many VP positions per symbol? Same signals, fixed 2R exits (live rules),
different limits on simultaneous VP positions per symbol. NO ORDERS are sent.
Setups: re-entry + breakout (POC bounce off, as in the app on 2026-09-25)."""
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import MetaTrader5 as mt5
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import atr_series, detect

UTC = timezone.utc
DAYS = 60
SYMBOLS = [a for a in sys.argv[1:] if not a.startswith("--")] or ["BTCUSDm", "ETHUSDm", "XAGUSDm"]
ENABLED = {"poc_bounce": False, "va_reversal": True, "va_breakout": "--rev" not in sys.argv}
MAX_HOLD = 36 * 3600
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=DAYS)

trades = {}                                   # sym -> [(entry_t, exit_t, dir, R, setup)]
for sym in SYMBOLS:
    si = mt5.symbol_info(sym)
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, start - timedelta(days=5), now)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start - timedelta(days=5), now)
    m15 = m15[m15["time"] + 900 <= now.timestamp()]
    atr_all = atr_series(m15)
    m1_day, m15_day = m1["time"] // 86400, m15["time"] // 86400
    days_with = sorted(d for d in set(m1_day.tolist()) if (m1_day == d).sum() >= 60)
    out = []
    for day in sorted(set(m15_day.tolist())):
        if day * 86400 < start.timestamp():
            continue
        prev = [d for d in days_with if d < day]
        if not prev:
            continue
        prof = compute_profile(m1[m1_day == prev[-1]])
        if prof is None:
            continue
        idx = np.where(m15_day == day)[0]
        tb, ta = m15[idx], atr_all[idx]
        for k in range(2, len(tb) + 1):
            s = detect(tb[:k], ta[:k], prof, enabled=ENABLED)
            if s is None or s.blocked:
                continue
            et = s.trigger_time + 900
            buy = s.direction == "buy"
            fwd = m1[np.searchsorted(m1["time"], et):np.searchsorted(m1["time"], et + MAX_HOLD)]
            r, xt = None, None
            for b in fwd:
                if (b["low"] <= s.sl) if buy else (b["high"] >= s.sl):
                    r, xt = -1.0, int(b["time"]); break
                if (b["high"] >= s.tp) if buy else (b["low"] <= s.tp):
                    r, xt = 2.0, int(b["time"]); break
            if r is None:
                if len(fwd) and et + MAX_HOLD <= now.timestamp():   # window complete (gaps ok)
                    last = float(fwd[-1]["close"])
                    r, xt = ((last - s.entry) if buy else (s.entry - last)) / s.risk, int(fwd[-1]["time"])
                else:
                    continue
            r -= float(tb[k - 1]["spread"]) * si.point / s.risk
            out.append((et, xt, s.direction, r, s.setup))
    trades[sym] = out
mt5.shutdown()


def run(max_total, max_same):
    taken = []
    peak_open = 0
    for sym, sigs in trades.items():
        open_ = []
        for et, xt, d, r, setup in sigs:
            open_ = [o for o in open_ if o[1] > et]
            if len(open_) >= max_total or sum(o[2] == d for o in open_) >= max_same:
                continue
            open_.append((et, xt, d))
            peak_open = max(peak_open, len(open_))
            taken.append((et, xt, d, r, sym))
    if not taken:
        return None
    r = np.array([t[3] for t in taken])
    # drawdown on the exit-time equity curve, all symbols together
    eq = np.cumsum([t[3] for t in sorted(taken, key=lambda z: z[1])])
    dd = (np.maximum.accumulate(np.concatenate(([0], eq)))[1:] - eq).max()
    # worst run of simultaneous open risk (all symbols) = losses that can land together
    ev = sorted([(t[0], 1) for t in taken] + [(t[1], -1) for t in taken])
    cur = worst = 0
    for _, x in ev:
        cur += x
        worst = max(worst, cur)
    gp, gl = r[r > 0].sum(), -r[r <= 0].sum()
    return (f"n={len(r):3d} WR={100 * (r > 0).mean():5.1f}% total={r.sum():+6.1f}R "
            f"avg={r.mean():+.3f}R PF={gp / gl:4.2f} maxDD={dd:4.1f}R  "
            f"max open/symbol={peak_open}  max open overall={worst}")


print(f"VP replay, last {DAYS} days, {'re-entry ONLY' if '--rev' in sys.argv else 're-entry + breakout'}, "
      f"fixed 2R, {'/'.join(s.replace('m', '') for s in SYMBOLS)}")
for sym, tr in trades.items():
    r = np.array([t[3] for t in tr]) if tr else np.zeros(0)
    print(f"  signals {sym}: {len(tr)}  (if every one were taken: {r.sum():+.1f}R)")
for label, mt, ms in (("1 per symbol (now)", 1, 1),
                      ("1 buy + 1 sell", 2, 1),
                      ("max 2, any side", 2, 2),
                      ("max 3, any side", 3, 3),
                      ("2 buys + 2 sells", 4, 2),
                      ("max 5, any side", 5, 5),
                      ("unlimited", 99, 99)):
    print(f"  {label:20s} {run(mt, ms)}")

