"""Would the SMC bot's trail help Volume Profile trades? NO ORDERS are sent.

Signals: production detector, current VP config (re-entry + breakout, POC off),
BTC ETH XAG XAU USDJPY, 60 days, 24/7, one VP position per symbol.

SMC trail as coded in router.manage_open_positions (runs every 30 s):
  arm when profit >= 1.0R (ETH 1.5R); then SL = price -/+ buffer,
  buffer = max(5% of the TP distance, 10% of current profit), floored at
  1.5 x ATR for XAU and 3.0 x ATR for ETH; SL only ratchets; TP stays.
Simulated on M1 bars: stop checked first (conservative), then target, then the
trail is updated from the bar CLOSE (the live bot samples the price every
30 s, it does not see every wick). Spread charged once, in R. Max hold 36 h.
"""
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
ENABLED = {"poc_bounce": False, "va_reversal": True, "va_breakout": True}
MAX_HOLD = 36 * 3600
ARM_BY_SYM = {"ETHUSD": 1.5}
FLOOR_BY_SYM = {"XAUUSD": 1.5, "ETHUSD": 3.0}

mt5.initialize(timeout=10000)
now = datetime.now(UTC)
start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=DAYS)

data = {}
for sym in SYMBOLS:
    si = mt5.symbol_info(sym)
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, start - timedelta(days=5), now)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start - timedelta(days=5), now)
    m15 = m15[m15["time"] + 900 <= now.timestamp()]
    atr_all = atr_series(m15)
    m1_day, m15_day = m1["time"] // 86400, m15["time"] // 86400
    days_with = sorted(d for d in set(m1_day.tolist()) if (m1_day == d).sum() >= 60)
    sigs = []
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
            sigs.append((s.trigger_time + 900, s, float(ta[k - 1]),
                         float(tb[k - 1]["spread"]) * si.point / s.risk))
    data[sym] = (m1, sigs)
    print(f"{sym}: {len(sigs)} signals")
mt5.shutdown()


def sym_val(table, sym, default):
    for k, v in table.items():
        if k in sym.upper():
            return v
    return default


def simulate(sym, sig, atr, fwd, v):
    """-> (R, exit_t, tag) or (None, None, 'open')."""
    buy = sig.direction == "buy"
    sgn = 1 if buy else -1
    e, risk = sig.entry, sig.risk
    sl = sig.sl
    tp_r = v["tp"]
    tp = e + sgn * tp_r * risk if tp_r else None
    total_dist = (tp_r or 2.0) * risk
    mode = v.get("trail")                          # None | "smc" | ("atr", k)
    arm = v.get("arm") or sym_val(ARM_BY_SYM, sym, 1.0) if mode == "smc" else v.get("arm", 1.0)
    floor = sym_val(FLOOR_BY_SYM, sym, 0.0) if mode == "smc" else 0.0
    moved_sl = False
    for b in fwd:
        t = int(b["time"])
        hi, lo, cl = float(b["high"]), float(b["low"]), float(b["close"])
        if (lo <= sl) if buy else (hi >= sl):
            return sgn * (sl - e) / risk, t, ("trail-stop" if moved_sl else "stop")
        if tp is not None and ((hi >= tp) if buy else (lo <= tp)):
            return tp_r, t, "target"
        if mode:
            moved = sgn * (cl - e)
            if moved >= arm * risk:
                if mode == "smc":
                    buf = max(0.05 * total_dist, 0.10 * moved)
                    if floor and floor * atr > buf:
                        buf = floor * atr
                else:
                    buf = mode[1] * atr
                cand = cl - sgn * buf
                if (cand > sl) if buy else (cand < sl):
                    sl, moved_sl = cand, True
    if len(fwd) and int(fwd[0]["time"]) + MAX_HOLD - 60 <= now.timestamp():
        return sgn * (float(fwd[-1]["close"]) - e) / risk, int(fwd[-1]["time"]), "timeout"
    return None, None, "open"


def run(v):
    out = []
    for sym, (m1, sigs) in data.items():
        busy_until = 0
        for et, s, atr, spr in sigs:
            if et < busy_until:
                continue
            fwd = m1[np.searchsorted(m1["time"], et):np.searchsorted(m1["time"], et + MAX_HOLD)]
            if len(fwd) == 0:
                continue                             # Friday-close signal, market shut
            r, xt, tag = simulate(sym, s, atr, fwd, v)
            if r is None:
                break
            busy_until = xt
            out.append(dict(sym=sym[:3], setup=s.setup, r=r - spr, tag=tag,
                            t=datetime.fromtimestamp(et, UTC)))
    return out


VARIANTS = {
    "A  fixed 2R (current)":                dict(tp=2.0),
    "S1 SMC trail as-is, TP 2R":             dict(tp=2.0, trail="smc"),
    "S2 SMC trail, TP 3R":                   dict(tp=3.0, trail="smc"),
    "S3 SMC trail, TP 4R":                   dict(tp=4.0, trail="smc"),
    "S4 SMC trail armed at 1.5R, TP 4R":     dict(tp=4.0, trail="smc", arm=1.5),
    "S5 SMC trail armed at 2R, TP 6R":       dict(tp=6.0, trail="smc", arm=2.0),
    "W1 1.0xATR trail from 1R, TP 4R":       dict(tp=4.0, trail=("atr", 1.0), arm=1.0),
    "W2 1.5xATR trail from 1.5R, TP 6R":     dict(tp=6.0, trail=("atr", 1.5), arm=1.5),
    "W3 2.0xATR trail from 2R, no TP":       dict(tp=None, trail=("atr", 2.0), arm=2.0),
    "F1 fixed 1.5R":                         dict(tp=1.5),
    "F2 fixed 3R":                           dict(tp=3.0),
}


def stats(v):
    r = np.array([x["r"] for x in v])
    w, l = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    dd = (np.maximum.accumulate(np.concatenate(([0], eq)))[1:] - eq).max()
    return (f"n={len(r):3d} WR={100 * (r > 0).mean():5.1f}% total={r.sum():+6.1f}R "
            f"avgWin={w.mean() if len(w) else 0:+.2f}R avgLoss={l.mean() if len(l) else 0:+.2f}R "
            f"PF={w.sum() / -l.sum() if l.sum() else 9.99:4.2f} maxDD={dd:4.1f}R")


print(f"\nVP EXIT RULES - {DAYS} days, re-entry + breakout, {'/'.join(s[:3] for s in SYMBOLS)}, "
      f"1 position per symbol, after spread")
res = {}
mid = start + (now - start) / 2
for name, v in VARIANTS.items():
    res[name] = run(v)
    a = res[name]
    h1 = sum(x["r"] for x in a if x["t"] < mid)
    h2 = sum(x["r"] for x in a if x["t"] >= mid)
    print(f"  {name:36s} {stats(a)}  halves {h1:+5.1f} / {h2:+5.1f}")

for name in ("A  fixed 2R (current)", "S1 SMC trail as-is, TP 2R"):
    print(f"\n{name} by symbol / setup / exit:")
    for s in sorted({x['sym'] for x in res[name]}):
        print(f"    {s:12s} {stats([x for x in res[name] if x['sym'] == s])}")
    for s in ("va_reversal", "va_breakout"):
        print(f"    {s:12s} {stats([x for x in res[name] if x['setup'] == s])}")
    tags = {}
    for x in res[name]:
        tags[x["tag"]] = tags.get(x["tag"], 0) + 1
    print("    exits: " + ", ".join(f"{k} {n}" for k, n in sorted(tags.items())))
