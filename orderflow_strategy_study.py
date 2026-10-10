"""ORDER FLOW AS ITS OWN STRATEGY (BTC, ETH, Binance 1m, Oct 2023 -> Oct 2026). Read-only.

Two strategies, every parameter fixed BEFORE any result was seen:

A. ABSORPTION REVERSAL at the previous UTC day's low / high
   long : a 15-min excursion trades below yesterday's low and a bar then CLOSES back
          above it (same bar allowed). Order-flow condition, measured over the
          excursion bars (first bar below -> reclaim bar): net aggressive SELLING
          delta share <= -0.10 AND average bar volume >= 1.2 x the average 15-min bar
          of the previous 24 h. Sellers were heavy and absorbed -> buy.
   short: mirror at yesterday's high (net BUYING >= +0.10).
   stop : beyond the excursion extreme + 0.1 ATR(15m). target 2R.

B. ORDER-FLOW MOMENTUM BREAKOUT
   long : a 15-min close above the highest high of the previous 16 bars (4 h), the
          first such close (previous bar did not close above its own 4 h high).
          Order-flow condition: delta share of the last 4 bars (1 h) >= +0.10 AND the
          breakout bar's volume >= 1.5 x the previous-24 h average bar.
   short: mirror.
   stop : beyond the extreme of the last 4 bars + 0.1 ATR. target 2R.

Each strategy is also run WITHOUT its order-flow condition (control), so the
contribution of order flow is measured directly.
Common: entry at the signal bar's close, Exness-like spread paid (BTC 0.0119 %, ETH
0.037 % of price), stop must be 0.25-4 ATR (the VP rule), stop/target on 1-min bars,
stop first on ties, 96 h max, 1 position per symbol per strategy at a time.

PASS (pre-registered) = test year (01/10/2025 -> 07/10/2026) avg >= +0.10R per trade
with >= 100 trades AND both test halves positive AND discovery avg > 0 AND better
than its control in discovery and in test."""
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vp_long_history_study as L

UTC = timezone.utc
TEST0 = datetime(2025, 10, 1, tzinfo=UTC).timestamp()
TEST_MID = datetime(2026, 4, 1, tzinfo=UTC).timestamp()
START = datetime(2023, 10, 4, tzinfo=UTC).timestamp()
rng = np.random.default_rng(9)
ABS_DELTA, ABS_RELVOL = 0.10, 1.2
MOM_DELTA, MOM_RELVOL = 0.10, 1.5
MIN_R, MAX_R = 0.25, 4.0


def share(v, tk):
    return (2 * tk - v) / v if v > 0 else 0.0


rows = []
for name in L.PAIRS:
    sym = L.Sym(name)
    b, atr = sym.m15, sym.atr
    t, o, h, l, c, v, tk = (b[k] for k in ("time", "open", "high", "low", "close", "vol", "taker"))
    cv = np.r_[0.0, np.cumsum(v)]
    avgv = np.full(len(b), np.nan)
    avgv[96:] = (cv[96:-1] - cv[:-97]) / 96                 # mean of the 96 bars BEFORE bar i
    day = t // 86400
    dmap = {int(x["time"] // 86400): (float(x["high"]), float(x["low"])) for x in sym.d1}

    def add(strategy, i, buy, sl, flow_ok, info):
        a = float(atr[i])
        entry = float(c[i]) * (1 + sym.sp) if buy else float(c[i])
        risk = (entry - sl) if buy else (sl - entry)
        if a <= 0 or not (MIN_R * a <= risk <= MAX_R * a) or t[i] < START:
            return
        res = sym.path(int(t[i]) + 900, entry, buy, risk)
        if res is None:
            return
        rows.append(dict(sym=name, strat=strategy, t=int(t[i]) + 900, dir="buy" if buy else "sell",
                         r2=res[2.0], x2=res["x2.0"], r1=res[1.0], flow=bool(flow_ok), **info))

    # ── A: absorption reversal at yesterday's low / high ────────────────────
    exc = {"buy": None, "sell": None}
    for i in range(97, len(b)):
        if day[i] != day[i - 1]:
            exc = {"buy": None, "sell": None}
        prev = dmap.get(int(day[i]) - 1)
        if prev is None:
            continue
        pdh, pdl = prev
        for buy in (True, False):
            k = "buy" if buy else "sell"
            beyond = (l[i] < pdl) if buy else (h[i] > pdh)
            if beyond and exc[k] is None:
                exc[k] = i
            if exc[k] is None:
                continue
            back = (c[i] > pdl) if buy else (c[i] < pdh)
            if not back:
                continue
            s0 = exc[k]
            exc[k] = None
            vv, tt = v[s0:i + 1].sum(), tk[s0:i + 1].sum()
            d = share(vv, tt)
            rel = (vv / (i + 1 - s0)) / avgv[s0] if avgv[s0] > 0 else 0
            ext = l[s0:i + 1].min() if buy else h[s0:i + 1].max()
            sl = ext - 0.1 * atr[i] if buy else ext + 0.1 * atr[i]
            flow_ok = (d <= -ABS_DELTA if buy else d >= ABS_DELTA) and rel >= ABS_RELVOL
            add("A absorption", i, buy, sl, flow_ok, dict(delta=d, relvol=rel))

    # ── B: order-flow momentum breakout ─────────────────────────────────────
    for i in range(113, len(b)):
        hh, ll = h[i - 16:i].max(), l[i - 16:i].min()
        hh_p, ll_p = h[i - 17:i - 1].max(), l[i - 17:i - 1].min()
        for buy in (True, False):
            brk = (c[i] > hh and c[i - 1] <= hh_p) if buy else (c[i] < ll and c[i - 1] >= ll_p)
            if not brk:
                continue
            d1h = share(v[i - 3:i + 1].sum(), tk[i - 3:i + 1].sum())
            rel = v[i] / avgv[i] if avgv[i] > 0 else 0
            sl = (l[i - 3:i + 1].min() - 0.1 * atr[i]) if buy else (h[i - 3:i + 1].max() + 0.1 * atr[i])
            flow_ok = (d1h >= MOM_DELTA if buy else d1h <= -MOM_DELTA) and rel >= MOM_RELVOL
            add("B momentum", i, buy, sl, flow_ok, dict(delta=d1h, relvol=rel))
    print(f"{name}: {sum(1 for r in rows if r['sym'] == name)} resolved signals "
          f"(A {sum(1 for r in rows if r['sym'] == name and r['strat'].startswith('A'))}, "
          f"B {sum(1 for r in rows if r['sym'] == name and r['strat'].startswith('B'))})")


def take(rs):
    busy, out = {}, []
    for r in sorted(rs, key=lambda r: r["t"]):
        if busy.get(r["sym"], 0) > r["t"]:
            continue
        busy[r["sym"]] = r["x2"]
        out.append(r)
    return out


def avg(rs):
    return float(np.mean([r["r2"] for r in rs])) if rs else float("nan")


def st(rs):
    if not rs:
        return "n=    0"
    x = np.array([r["r2"] for r in rs])
    boot = [rng.choice(x, len(x)).mean() for _ in range(1500)]
    gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
    return (f"n={len(x):5d} WR {100 * (x > 0).mean():3.0f}% avg {x.mean():+.3f}R "
            f"[90% CI {np.percentile(boot, 5):+.3f}..{np.percentile(boot, 95):+.3f}] "
            f"sum {x.sum():+7.1f}R PF {gp / gl if gl else 9:4.2f}")


ts = lambda y, m: datetime(y, m, 1, tzinfo=UTC).timestamp()
PER = [("Q4 23", ts(2023, 10), ts(2024, 1)), ("24 H1", ts(2024, 1), ts(2024, 7)), ("24 H2", ts(2024, 7), ts(2025, 1)),
       ("25 H1", ts(2025, 1), ts(2025, 7)), ("25 Q3", ts(2025, 7), ts(2025, 10)), ("25Q4-26Q1", ts(2025, 10), ts(2026, 4)),
       ("26 Q2+", ts(2026, 4), ts(2027, 1))]
for strat in ("A absorption", "B momentum"):
    print("\n" + "=" * 110 + f"\n{strat.upper()}\n" + "=" * 110)
    allr = [r for r in rows if r["strat"] == strat]
    res = {}
    for lab, f in (("control (no order flow)", lambda r: True), ("WITH order-flow condition", lambda r: r["flow"])):
        sel = [r for r in allr if f(r)]
        dsc, tst = take([r for r in sel if r["t"] < TEST0]), take([r for r in sel if r["t"] >= TEST0])
        res[lab] = (dsc, tst)
        print(f"  {lab}")
        print(f"    discovery {st(dsc)}")
        print(f"    test year {st(tst)}   halves {sum(r['r2'] for r in tst if r['t'] < TEST_MID):+.1f} / "
              f"{sum(r['r2'] for r in tst if r['t'] >= TEST_MID):+.1f}R")
        both = take(sel)
        print("    per period " + "  ".join(f"{n} {sum(r['r2'] for r in both if a <= r['t'] < b):+6.1f}" for n, a, b in PER))
        for s in L.PAIRS:
            print(f"    {s}: discovery {avg([r for r in dsc if r['sym'] == s]):+.3f}R  test {avg([r for r in tst if r['sym'] == s]):+.3f}R")
    # +1R-before-1R: does the order-flow condition improve direction at all (every signal)?
    for lab, f in (("control", lambda r: True), ("order flow", lambda r: r["flow"])):
        x = [r for r in allr if f(r)]
        print(f"  +1R before -1R ({lab:10s}): discovery {100 * np.mean([r['r1'] > 0 for r in x if r['t'] < TEST0]):5.1f}%  "
              f"test {100 * np.mean([r['r1'] > 0 for r in x if r['t'] >= TEST0]):5.1f}%  (n={len(x)})")
    (cd, ct), (fd, ft) = res["control (no order flow)"], res["WITH order-flow condition"]
    h1 = sum(r["r2"] for r in ft if r["t"] < TEST_MID)
    h2 = sum(r["r2"] for r in ft if r["t"] >= TEST_MID)
    checks = {"test avg >= +0.10R": avg(ft) >= 0.10, "test trades >= 100": len(ft) >= 100,
              "both test halves > 0": h1 > 0 and h2 > 0, "discovery avg > 0": avg(fd) > 0,
              "beats control (discovery)": avg(fd) > avg(cd), "beats control (test)": avg(ft) > avg(ct)}
    print("  PRE-REGISTERED CHECKS: " + " | ".join(f"{k}: {'yes' if v else 'NO'}" for k, v in checks.items()))
    print(f"  VERDICT: {'PASS' if all(checks.values()) else 'FAIL'}")
