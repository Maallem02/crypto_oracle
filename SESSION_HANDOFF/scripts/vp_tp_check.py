"""Is 1.5R better than 2R for VP re-entries? Live trades (31) said 1.5R; check it
on 60 days of signals from the production detector. Re-entry only, 1 position
per symbol (blocking computed per exit rule), spread charged, stop first on
ties, M1 exits, 96 h max hold. Halves + per symbol, so noise shows as noise."""
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import MetaTrader5 as mt5
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import atr_series, detect

UTC = timezone.utc
DAYS = 60
SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAUUSDm", "USDJPYm", "XAGUSDm"]
TPS = (1.0, 1.5, 2.0, 2.5, 3.0)
MAX_HOLD = 96 * 3600
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=DAYS)
MID = (start + (now - start) / 2).timestamp()


def exit_r(sig, fwd, tp):
    buy = sig.direction == "buy"
    sgn = 1 if buy else -1
    for b in fwd:
        fav = sgn * ((b["high"] if buy else b["low"]) - sig.entry) / sig.risk
        adv = sgn * ((b["low"] if buy else b["high"]) - sig.entry) / sig.risk
        if adv <= -1.0:
            return -1.0, int(b["time"])
        if fav >= tp:
            return tp, int(b["time"])
    if len(fwd) and int(fwd[0]["time"]) + MAX_HOLD - 60 <= now.timestamp():
        return sgn * (float(fwd[-1]["close"]) - sig.entry) / sig.risk, int(fwd[-1]["time"])
    return None, None


sigs = []
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
            if s is None or s.blocked or s.setup != "va_reversal":
                continue
            et = s.trigger_time + 900
            fwd = m1[np.searchsorted(m1["time"], et):np.searchsorted(m1["time"], et + MAX_HOLD)]
            if len(fwd) == 0:
                continue                                   # Friday close: market shut
            spr = float(tb[k - 1]["spread"]) * si.point / s.risk
            ex = {tp: exit_r(s, fwd, tp) for tp in TPS}
            if any(v[0] is None for v in ex.values()):
                continue                                   # still open today
            sigs.append(dict(sym=sym[:-1], dir=s.direction, t=et,
                             **{f"r{tp}": ex[tp][0] - spr for tp in TPS},
                             **{f"x{tp}": ex[tp][1] for tp in TPS}))
mt5.shutdown()
sigs.sort(key=lambda x: x["t"])


def run(tp, symbols):
    taken, busy = [], {}
    for s in sigs:
        if s["sym"] not in symbols or busy.get(s["sym"], 0) > s["t"]:
            continue
        busy[s["sym"]] = s[f"x{tp}"]
        taken.append({**s, "r": s[f"r{tp}"]})
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
    return (f"n={len(r):3d} WR={100 * (r > 0).mean():3.0f}% total={r.sum():+6.1f}R PF={gp / gl if gl else 9.99:4.2f} "
            f"maxDD={dd:4.1f}R halves {h1:+5.1f}/{h2:+5.1f}")


print(f"{len(sigs)} resolved re-entry signals, {start:%d/%m} -> {now:%d/%m}")
for label, syms in (("LIVE 4 symbols (BTC ETH XAU USDJPY)", {"BTCUSD", "ETHUSD", "XAUUSD", "USDJPY"}),
                    ("with XAG too", {"BTCUSD", "ETHUSD", "XAUUSD", "USDJPY", "XAGUSD"})):
    print(f"\n{label}")
    for tp in TPS:
        print(f"  target {tp:.1f}R  {stats(run(tp, syms))}")
print("\nper symbol (1.5R vs 2R):")
for s in ("BTCUSD", "ETHUSD", "XAUUSD", "USDJPY", "XAGUSD"):
    print(f"  {s:7s} 1.5R {stats(run(1.5, {s}))}\n  {'':7s} 2.0R {stats(run(2.0, {s}))}")
