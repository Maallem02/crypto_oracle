"""Same VP signals, different trade management. NO ORDERS are sent.

Signals come from the production detector (strategy.detect) on closed M15 bars,
levels from the previous UTC session, 24/7 like the live VP bot. Each variant
runs its own sequential simulation per symbol (one VP position per symbol), so
an exit rule that frees the slot earlier can also take the next signal.

Exits walk M1 bars. Conservative tie rules: if a bar touches both the stop and
the target the STOP wins; a stop moved (break-even / trail) only applies from
the NEXT M1 bar after the trigger. Spread charged once per trade, in R.
Max hold 36 h, then market exit.
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import MetaTrader5 as mt5
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import atr_series, detect

UTC = timezone.utc
DAYS = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--days=")), 60))
SYMBOLS = [a for a in sys.argv[1:] if not a.startswith("--")] or ["BTCUSDm", "ETHUSDm", "XAGUSDm"]
LVL_BUF = 0.10       # front-run the target level by 0.1 ATR
MAX_HOLD = 36 * 3600

mt5.initialize(timeout=10000)
now = datetime.now(UTC)
start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=DAYS)


def level_target(sig, prof, a):
    """Price level the trade is travelling toward, or None (-> fixed 2R)."""
    buy = sig.direction == "buy"
    if sig.setup == "va_reversal":                      # 80 % rule: the other side of the VA
        lv = prof.vah - LVL_BUF * a if buy else prof.val + LVL_BUF * a
    elif sig.setup == "poc_bounce":                     # next profile level in the way
        cands = [x for x in (prof.val, prof.poc, prof.vah)
                 if (x > sig.entry + 0.1 * a if buy else x < sig.entry - 0.1 * a)]
        if not cands:
            return None
        x = min(cands) if buy else max(cands)
        lv = x - LVL_BUF * a if buy else x + LVL_BUF * a
    else:
        return None                                     # breakout: nothing ahead
    return lv if (lv > sig.entry if buy else lv < sig.entry) else None


def midpoint(sig, prof):
    """For a reversal trade: the POC, if it lies between entry and target."""
    if sig.setup != "va_reversal":
        return None
    buy = sig.direction == "buy"
    return prof.poc if (prof.poc > sig.entry if buy else prof.poc < sig.entry) else None


# ── variants ────────────────────────────────────────────────────────────────
#   tp:    "2R" | "level" | "none"
#   be:    None | R multiple that moves the stop to entry | "poc"
#   trail: None | R multiple after which the stop trails the last 2 closed M15 bars
#   part:  None | "poc1R" -> close half at POC (reversal) / 1R (others), stop -> entry
#   skip_below: skip the trade when the level target gives < this many R
#   sar:   close the open trade when an opposite VP signal fires, take the new one
VARIANTS = {
    "A  fixed 2R (current)":            dict(tp="2R"),
    "B  2R + BE at 1R":                 dict(tp="2R", be=1.0),
    "C  level target":                  dict(tp="level"),
    "D  level target, skip <1R":        dict(tp="level", skip_below=1.0),
    "E  level + BE at 1R":              dict(tp="level", be=1.0),
    "F  level + BE at POC":             dict(tp="level", be="poc"),
    "G  level + half at POC/1R + BE":   dict(tp="level", part="poc1R"),
    "H  level + trail after 1R":        dict(tp="level", trail=1.0),
    "I  2R + trail after 1R":           dict(tp="2R", trail=1.0),
    "J  level, brk trails (no TP)":     dict(tp="level", brk_trail=True),
    "K  = E + stop-and-reverse":        dict(tp="level", be=1.0, sar=True),
    "L  = C + stop-and-reverse":        dict(tp="level", sar=True),
    "M  = A + stop-and-reverse":        dict(tp="2R", sar=True),
    "N  nearer of level / 2R":          dict(tp="min"),
    "O  farther of level / 2R":         dict(tp="max"),
    "P  = N + stop-and-reverse":        dict(tp="min", sar=True),
}
ONLY = next((a.split("=")[1].split(",") for a in sys.argv if a.startswith("--setups=")), None)


def simulate(sig, prof, a, fwd, m15, v, cut_t=None, cut_px=None):
    """Return (R, exit_t, tag) for one trade. cut_t/cut_px: forced market exit."""
    buy = sig.direction == "buy"
    e, risk = sig.entry, sig.risk
    sgn = 1 if buy else -1
    sl = sig.sl
    lvl = level_target(sig, prof, a)
    if v.get("tp") == "level" and lvl is not None:
        tp = lvl
    elif v.get("tp") in ("min", "max") and lvl is not None:
        two = e + sgn * 2 * risk
        pick = min if (v["tp"] == "min") == buy else max
        tp = pick(lvl, two)
    elif sig.setup == "va_breakout" and v.get("brk_trail"):
        tp = None
    else:
        tp = e + sgn * 2 * risk
    mid = midpoint(sig, prof)
    be_px = None
    if v.get("be") == "poc":
        be_px = mid if mid is not None else e + sgn * 1.0 * risk
    elif v.get("be"):
        be_px = e + sgn * v["be"] * risk
    trail_from = e + sgn * v["trail"] * risk if v.get("trail") else None
    if v.get("brk_trail") and sig.setup == "va_breakout":
        trail_from = e + sgn * 1.0 * risk
    part_px = None
    if v.get("part"):
        part_px = mid if mid is not None else e + sgn * 1.0 * risk
    banked, size = 0.0, 1.0
    pending_sl = None                      # stop change that applies from the next bar
    trailing = False
    for b in fwd:
        t = int(b["time"])
        if cut_t is not None and t >= cut_t:
            return banked + size * sgn * (cut_px - e) / risk, t, "reversed"
        if pending_sl is not None:
            sl = max(sl, pending_sl) if buy else min(sl, pending_sl)
            pending_sl = None
        if trailing:
            k = np.searchsorted(m15["time"], t - 900, side="right")   # closed M15 bars
            if k >= 2:
                ext = m15["low"][k - 2:k].min() if buy else m15["high"][k - 2:k].max()
                cand = ext - 0.1 * a if buy else ext + 0.1 * a
                sl = max(sl, cand) if buy else min(sl, cand)
        hi, lo = float(b["high"]), float(b["low"])
        fav, adv = (hi, lo) if buy else (lo, hi)
        if (adv <= sl) if buy else (adv >= sl):
            return banked + size * sgn * (sl - e) / risk, t, "stop" if sl == sig.sl else "moved-stop"
        if tp is not None and ((fav >= tp) if buy else (fav <= tp)):
            return banked + size * sgn * (tp - e) / risk, t, "target"
        if part_px is not None and size == 1.0 and ((fav >= part_px) if buy else (fav <= part_px)):
            banked, size = 0.5 * sgn * (part_px - e) / risk, 0.5
            pending_sl = e
        if be_px is not None and ((fav >= be_px) if buy else (fav <= be_px)):
            pending_sl = e
            be_px = None
        if trail_from is not None and not trailing and ((fav >= trail_from) if buy else (fav <= trail_from)):
            trailing = True
    if len(fwd) and int(fwd[0]["time"]) + MAX_HOLD - 60 <= now.timestamp():   # window complete
        last = float(fwd[-1]["close"])
        return banked + size * sgn * (last - e) / risk, int(fwd[-1]["time"]), "timeout"
    return None, None, "open"                 # too recent to resolve


# ── collect signals once ────────────────────────────────────────────────────
data = {}
for sym in SYMBOLS:
    si = mt5.symbol_info(sym)
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, start - timedelta(days=5), now)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start - timedelta(days=5), now)
    if m1 is None or m15 is None or len(m1) < 1000:
        print(f"{sym}: not enough history")
        continue
    m15 = m15[m15["time"] + 900 <= now.timestamp()]
    atr_all = atr_series(m15)
    m1_day = m1["time"] // 86400
    m15_day = m15["time"] // 86400
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
            s = detect(tb[:k], ta[:k], prof,
                       enabled=({x: x in ONLY for x in ("poc_bounce", "va_reversal", "va_breakout")}
                                if ONLY else None))
            if s is None or s.blocked:
                continue
            sigs.append((s.trigger_time + 900, s, prof, float(ta[k - 1]),
                         float(tb[k - 1]["spread"]) * si.point / s.risk))
    data[sym] = (m1, m15, sigs)
    print(f"{sym}: M1 from {datetime.fromtimestamp(int(m1['time'][0]), UTC):%Y-%m-%d}, "
          f"{len(sigs)} tradable signals")
mt5.shutdown()


def run(v):
    out = []
    for sym, (m1, m15, sigs) in data.items():
        i = 0
        while i < len(sigs):
            et, s, prof, a, spr = sigs[i]
            lvl = level_target(s, prof, a)
            if v.get("skip_below") and lvl is not None and abs(lvl - s.entry) / s.risk < v["skip_below"]:
                i += 1
                continue
            j0 = np.searchsorted(m1["time"], et)
            fwd = m1[j0:np.searchsorted(m1["time"], et + MAX_HOLD)]
            if len(fwd) == 0 and et + MAX_HOLD <= now.timestamp():
                i += 1                              # Friday-close signal: market shut, never filled
                continue
            r, xt, tag = simulate(s, prof, a, fwd, m15, v)
            if r is None:
                break
            nxt = i + 1
            if v.get("sar"):
                # first opposite signal before this trade's exit -> reverse there
                for j in range(i + 1, len(sigs)):
                    et2, s2 = sigs[j][0], sigs[j][1]
                    if et2 >= xt:
                        break
                    if s2.direction != s.direction:
                        r, xt, tag = simulate(s, prof, a, fwd, m15, v, cut_t=et2, cut_px=s2.entry)
                        nxt = j
                        break
            if r is None:
                break
            out.append(dict(sym=sym[:3], setup=s.setup, r=r - spr, tag=tag,
                            t=datetime.fromtimestamp(et, UTC)))
            if nxt == i + 1:                        # skip signals while this trade is open
                nxt = i + 1
                while nxt < len(sigs) and sigs[nxt][0] < xt:
                    nxt += 1
            i = nxt
    return out


def stats(v):
    if not v:
        return "n=  0"
    r = np.array([x["r"] for x in v])
    gp, gl = r[r > 0].sum(), -r[r <= 0].sum()
    eq = np.cumsum(r)
    dd = (np.maximum.accumulate(np.concatenate(([0], eq)))[1:] - eq).max()
    return (f"n={len(r):3d} WR={100 * (r > 0).mean():5.1f}% total={r.sum():+6.1f}R "
            f"avg={r.mean():+.3f}R PF={(gp / gl if gl else 9.99):4.2f} maxDD={dd:4.1f}R")


print("\n" + "=" * 110)
print(f"TRADE MANAGEMENT COMPARISON - same signals, last {DAYS} days, 24/7, after spread")
print("=" * 110)
res = {}
for name, v in VARIANTS.items():
    res[name] = run(v)
    print(f"{name:34s} {stats(res[name])}")

for name in ("A  fixed 2R (current)", "C  level target", "E  level + BE at 1R", "L  = C + stop-and-reverse"):
    print(f"\n{name} by setup / symbol / exit:")
    for s in ("poc_bounce", "va_reversal", "va_breakout"):
        print(f"    {s:14s} {stats([x for x in res[name] if x['setup'] == s])}")
    for s in sorted({x['sym'] for x in res[name]}):
        print(f"    {s:14s} {stats([x for x in res[name] if x['sym'] == s])}")
    tags = defaultdict(int)
    for x in res[name]:
        tags[x["tag"]] += 1
    print("    exits: " + ", ".join(f"{k} {n}" for k, n in sorted(tags.items())))

# second half vs first half, for robustness
print("\nSTABILITY - first vs second half of the window:")
mid_t = start + (now - start) / 2
for name in VARIANTS:
    a1 = [x for x in res[name] if x["t"] < mid_t]
    a2 = [x for x in res[name] if x["t"] >= mid_t]
    print(f"  {name:34s} 1st: {sum(x['r'] for x in a1):+6.1f}R ({len(a1)})   "
          f"2nd: {sum(x['r'] for x in a2):+6.1f}R ({len(a2)})")
