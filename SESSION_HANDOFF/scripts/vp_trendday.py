"""VP improvement options, tested TOGETHER on the same 60 days / 5 live symbols.
Production detector, 24/7, spread charged, stop first on ties, M1 exits.
Config axes: setups, positions per symbol, exit (2R / 2R + BE at +1R).
Then, for the best config: per symbol, stop width, hour of day â€” with halves,
so an effect that only exists in one half is visible as such."""
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import MetaTrader5 as mt5
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import atr_series, detect

UTC = timezone.utc
DAYS = 60
SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAGUSDm", "XAUUSDm", "USDJPYm"]
MAX_HOLD = 36 * 3600
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=DAYS)
MID = (start + (now - start) / 2).timestamp()


def exit_r(sig, fwd, be_at=None):
    buy = sig.direction == "buy"
    sgn = 1 if buy else -1
    e, risk = sig.entry, sig.risk
    stop, pend, mfe = -1.0, None, 0.0
    for b in fwd:
        if pend is not None:
            stop, pend = max(stop, pend), None
        fav = sgn * ((b["high"] if buy else b["low"]) - e) / risk
        adv = sgn * ((b["low"] if buy else b["high"]) - e) / risk
        if adv <= stop:
            return stop, int(b["time"])
        if fav >= 2.0:
            return 2.0, int(b["time"])
        mfe = max(mfe, fav)
        if be_at and mfe >= be_at:
            pend = 0.05
    if len(fwd) and int(fwd[0]["time"]) + MAX_HOLD - 60 <= now.timestamp():
        return sgn * (float(fwd[-1]["close"]) - e) / risk, int(fwd[-1]["time"])
    return None, None


sigs = []                                              # every tradable signal, both setups
for sym in SYMBOLS:
    si = mt5.symbol_info(sym)
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, start - timedelta(days=5), now)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start - timedelta(days=5), now)
    m15 = m15[m15["time"] + 900 <= now.timestamp()]
    atr_all = atr_series(m15)
    m1_day, m15_day = m1["time"] // 86400, m15["time"] // 86400
    days_with = sorted(d for d in set(m1_day.tolist()) if (m1_day == d).sum() >= 60)
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
            s = detect(tb[:k], ta[:k], prof, enabled={"poc_bounce": False, "va_breakout": False})
            if s is None or s.blocked:
                continue
            et = s.trigger_time + 900
            fwd = m1[np.searchsorted(m1["time"], et):np.searchsorted(m1["time"], et + MAX_HOLD)]
            if len(fwd) == 0:
                continue                                   # Friday close: market shut
            spr = float(tb[k - 1]["spread"]) * si.point / s.risk
            r2, x2 = exit_r(s, fwd)
            rb, xb = exit_r(s, fwd, be_at=1.0)
            if r2 is None or rb is None:
                continue
            sigs.append(dict(sym=sym[:-1], setup=s.setup, dir=s.direction, t=et,
                             hour=datetime.fromtimestamp(et, UTC).hour,
                             risk_atr=s.detail.get("risk_atr") or 0, day_move=(s.entry - float(tb[0]["open"])) / float(ta[k - 1]),
                             r2=r2 - spr, x2=x2, rb=rb - spr, xb=xb))
mt5.shutdown()
sigs.sort(key=lambda x: x["t"])
print(f"{len(sigs)} resolved signals "
      f"(re-entry {sum(s['setup'] == 'va_reversal' for s in sigs)}, "
      f"breakout {sum(s['setup'] == 'va_breakout' for s in sigs)})")


def run(setups, max_total, max_same, exit_key):
    rk, xk = ("r2", "x2") if exit_key == "2R" else ("rb", "xb")
    taken, open_ = [], {}
    for s in sigs:
        if s["setup"] not in setups:
            continue
        op = [o for o in open_.get(s["sym"], []) if o[0] > s["t"]]
        if len(op) >= max_total or sum(o[1] == s["dir"] for o in op) >= max_same:
            open_[s["sym"]] = op
            continue
        op.append((s[xk], s["dir"]))
        open_[s["sym"]] = op
        taken.append({**s, "r": s[rk]})
    return taken


def stats(rows):
    if not rows:
        return "n=  0"
    r = np.array([x["r"] for x in rows])
    eq = np.cumsum(r)
    dd = (np.maximum.accumulate(np.concatenate(([0], eq)))[1:] - eq).max()
    gp, gl = r[r > 0].sum(), -r[r <= 0].sum()
    h1 = sum(x["r"] for x in rows if x["t"] < MID)
    h2 = sum(x["r"] for x in rows if x["t"] >= MID)
    return (f"n={len(r):3d} WR={100 * (r > 0).mean():4.0f}% total={r.sum():+6.1f}R "
            f"PF={gp / gl if gl else 9.99:4.2f} maxDD={dd:4.1f}R  halves {h1:+6.1f} / {h2:+6.1f}")


REV, BRK = {"va_reversal"}, {"va_breakout"}
CONFIGS = [
    ("NOW: re-entry + breakout, 2/symbol same-dir, 2R", REV | BRK, 2, 2, "2R"),
    ("re-entry + breakout, 1/symbol, 2R",              REV | BRK, 1, 1, "2R"),
    ("re-entry only, 2/symbol same-dir, 2R",           REV, 2, 2, "2R"),
    ("re-entry only, 1 buy + 1 sell, 2R",              REV, 2, 1, "2R"),
    ("re-entry only, 1/symbol, 2R",                    REV, 1, 1, "2R"),
    ("re-entry only, 1/symbol, 2R + BE at +1R",        REV, 1, 1, "BE"),
    ("re-entry only, 1 buy + 1 sell, 2R + BE at +1R",  REV, 2, 1, "BE"),
]
print(f"\nVP OPTIONS â€” {DAYS} days, BTC/ETH/XAG/XAU/USDJPY (R = the trade's risk)")
res = {}
for name, st, mt, ms, ex in CONFIGS:
    res[name] = run(st, mt, ms, ex)
    print(f"  {name:50s} {stats(res[name])}")

best = res["re-entry only, 1/symbol, 2R"]
print("\nre-entry only, 1/symbol, 2R — by the day's move at entry (entry vs today's open, in ATR of 15m):")
def with_day(x):
    return (x["dir"] == "buy") == (x["day_move"] > 0)
print(f"    WITH the day's move     {stats([x for x in best if with_day(x)])}")
print(f"    AGAINST the day's move  {stats([x for x in best if not with_day(x)])}")
for lo, hi in ((0, 2), (2, 4), (4, 6), (6, 99)):
    print(f"    against, day moved {lo}-{hi} ATR  {stats([x for x in best if not with_day(x) and lo <= abs(x['day_move']) < hi])}")
for lo, hi in ((0, 2), (2, 4), (4, 6), (6, 99)):
    print(f"    with,    day moved {lo}-{hi} ATR  {stats([x for x in best if with_day(x) and lo <= abs(x['day_move']) < hi])}")
