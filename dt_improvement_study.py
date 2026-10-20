"""Daily Trend — three improvement ideas, tested against criteria WRITTEN BEFORE the run
(2026-10-18, user: "start daily trend test"). Output: data/dt_improvement_out.txt.

PRE-REGISTRATION (fixed before any result was seen)
  Baseline B0 = the bot today: Donchian 20/10, long only, BTC + ETH, stop 2 x ATR14,
     entry at the next daily open, 10-day-low exit at the close, 2% risk per trade,
     shared crypto cap 3% of equity (risk measured to each unit's current stop).
  V1 two channels: systems 20/10 and 55/20 run side by side, each with HALF the risk (1%).
  V2 volatility regime: risk % x clip(median 1-year ATR% / today's ATR%, 0.5, 1.5)
     (the bot already sizes by volatility through its 2-ATR stop; this adds the regime step).
  V3 add to winners: when a close is >= last entry + 2 ATR (+1R), add a HALF unit (1%) at
     the next open, at most 2 adds; every stop of the position is raised to the add
     price - 2 ATR (never lowered).
  Main data: Binance real daily prices, SPOT, $200 from 01/03/2018, fee 0.1% each way, no
     leverage (orders cut to the cash available), $5 minimum order.
  Second data: Exness daily prices, CFD costs (spread at entry, nightly swap), leverage allowed.
  A variant PASSES only if ALL five hold:
     C1 MAR (yearly return / worst drop) at least 10% better than B0 (main data, full period)
     C2 Sharpe >= B0 in BOTH halves (03/2018-12/2021 and 01/2022-now)
     C3 Sharpe >= B0 with 2021 removed
     C4 Sharpe >= B0 on the Exness data
     C5 worst drop no more than 3 points deeper than B0
  If more than one variant passes, their combination is tested next (not before).
"""
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from features.daily_trend.audit import reference_trades
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
OUT = os.path.join(HERE, "data", "dt_improvement_out.txt")


def ts(y, m=1, d=1):
    return int(datetime(y, m, d, tzinfo=UTC).timestamp())


START, HALF = ts(2018, 3, 1), ts(2022, 1, 1)
Y2021 = (ts(2021), ts(2022))
CAPITAL, BASE, CAP = 200.0, 0.02, 0.03
FEE, MIN_ORDER = 0.001, 5.0
EXNESS_COST = {"BTC": (0.00018, 0.000213), "ETH": (0.00060, 0.000210)}       # spread, swap / night (study)
COINS = ("BTC", "ETH")
VARIANTS = {
    "B0 baseline (bot today)": dict(systems=[(20, 10, 1.0)], vol=False, adds=0),
    "V1 two channels 20/10 + 55/20": dict(systems=[(20, 10, 0.5), (55, 20, 0.5)], vol=False, adds=0),
    "V2 risk x volatility regime": dict(systems=[(20, 10, 1.0)], vol=True, adds=0),
    "V3 add to winners (2 x half)": dict(systems=[(20, 10, 1.0)], vol=False, adds=2),
}


def load(kind):
    if kind == "binance":
        return {c: np.load(os.path.join(HERE, "data", f"binance_d1_{c}USDT.npy")) for c in COINS}
    return {c: np.load(os.path.join(HERE, "data", "multi_d1", f"{c}USDm.npy")) for c in COINS}


def simulate(data, cfg, spot):
    D = {}
    for c, d in data.items():
        atr = atr_series(d)
        v = atr / d["close"].astype(float)
        D[c] = dict(t=d["time"].astype(np.int64), o=d["open"].astype(float), h=d["high"].astype(float),
                    l=d["low"].astype(float), c=d["close"].astype(float), atr=atr, v=v,
                    idx={int(x): i for i, x in enumerate(d["time"])})
    days = sorted({int(x) for c in D for x in D[c]["t"]})
    cash, positions, pending, trades = CAPITAL, {}, [], []
    last_close = {c: None for c in D}
    curve, n = [], dict(entries=0, adds=0, cap_skip=0, small_skip=0, cash_cut=0)

    def equity():
        e = cash
        for (c, _), p in positions.items():
            for u in p["units"]:
                e += u["qty"] * (last_close[c] if spot else (last_close[c] - u["cost"]))
        return e

    def open_risk():
        return sum(max(0.0, u["px"] - u["stop"]) * u["qty"] for p in positions.values() for u in p["units"])

    def close_unit(c, s, p, u, px, k, kind):
        nonlocal cash
        cash += u["qty"] * px * (1 - FEE) if spot else u["qty"] * (px - u["cost"])
        trades.append(dict(coin=c, sys=s, t0=int(D[c]["t"][u["k"]]), t1=int(D[c]["t"][k]), kind=kind,
                           first=u is p["units"][0] and p.get("adds_done", 0) == 0))

    eq_prev = CAPITAL
    for day in days:
        # 1) orders decided at yesterday's close fill at today's open
        todo, pending = pending, []
        for kind, c, s, k, dist, w in todo:
            if D[c]["idx"].get(day) != k:
                continue
            eq = eq_prev
            target = BASE * w
            allowed = min(target, CAP - open_risk() / eq) if eq > 0 else 0.0
            if allowed <= 1e-4:
                n["cap_skip"] += 1
                continue
            px = D[c]["o"][k]
            qty = allowed * eq / (dist + (2 * FEE * px if spot else 0.0))
            if spot:
                afford = cash / (px * (1 + FEE))
                if qty > afford:
                    qty, n["cash_cut"] = afford * 0.999, n["cash_cut"] + 1
                if qty * px < MIN_ORDER:
                    n["small_skip"] += 1
                    continue
                cash -= qty * px * (1 + FEE)
                cost = px
            else:
                cost = px * (1 + EXNESS_COST[c][0])
            stop = (px if spot else cost) - dist
            u = dict(qty=qty, px=px, cost=cost, stop=stop, k=k)
            if kind == "entry":
                positions[(c, s)] = dict(units=[u], k_first=k, last_entry=px, last_dist=dist, adds_done=0)
                n["entries"] += 1
            else:
                p = positions.get((c, s))
                if p is None:
                    continue
                p["units"].append(u)
                p["adds_done"] += 1
                p["last_entry"], p["last_dist"] = px, dist
                for x in p["units"]:
                    x["stop"] = max(x["stop"], px - dist)
                n["adds"] += 1
        # 2) intraday stops, 3) channel exits at the close, 4) swap, mark to market
        exited = set()
        for c in COINS:
            k = D[c]["idx"].get(day)
            if k is None:
                continue
            d = D[c]
            for (cc, s), p in list(positions.items()):
                if cc != c:
                    continue
                keep = []
                for u in p["units"]:
                    if d["l"][k] <= u["stop"]:
                        px = u["stop"] if k == u["k"] else min(u["stop"], d["o"][k])
                        close_unit(c, s, p, u, px, k, "stop")
                    else:
                        keep.append(u)
                p["units"] = keep
                M = cfg["systems"][s][1]
                if keep and k > p["k_first"] and d["c"][k] < d["l"][k - M:k].min():
                    for u in keep:
                        close_unit(c, s, p, u, d["c"][k], k, "channel")
                    p["units"] = []
                if not p["units"]:
                    del positions[(c, s)]
                    exited.add((c, s))
                elif not spot:
                    for u in p["units"]:
                        if k > u["k"]:
                            cash -= EXNESS_COST[c][1] * d["c"][k] * u["qty"]
            last_close[c] = d["c"][k]
        eq_prev = equity()
        if day >= START:
            curve.append((day, eq_prev))
        # 5) decisions at the close for tomorrow's open
        for c in COINS:
            k = D[c]["idx"].get(day)
            if k is None or k + 1 >= len(D[c]["t"]):
                continue
            d = D[c]
            for s, (N, M, w) in enumerate(cfg["systems"]):
                p = positions.get((c, s))
                if p is not None:
                    if cfg["adds"] and p["adds_done"] < cfg["adds"] and d["c"][k] >= p["last_entry"] + p["last_dist"]:
                        pending.append(("add", c, s, k + 1, 2.0 * d["atr"][k], 0.5 * w))
                elif (c, s) not in exited and k >= max(N, 21) + 1 and d["c"][k] > d["h"][k - N:k].max():
                    wv = w
                    if cfg["vol"]:
                        med = float(np.median(d["v"][max(0, k - 364):k + 1]))
                        wv = w * float(np.clip(med / d["v"][k], 0.5, 1.5))
                    pending.append(("entry", c, s, k + 1, 2.0 * d["atr"][k], wv))
    return np.array([x[0] for x in curve]), np.array([x[1] for x in curve]), trades, n


def stats(days, eq, lo=None, hi=None, skip=None):
    sel = np.ones(len(days), bool)
    if lo is not None:
        sel &= days >= lo
    if hi is not None:
        sel &= days < hi
    e, d = eq[sel], days[sel]
    r = e[1:] / e[:-1] - 1
    if skip is not None:
        keep = ~((d[1:] >= skip[0]) & (d[1:] < skip[1]))
        r = r[keep]
    yrs = len(r) / 365.25
    growth = float(np.prod(1 + r))
    cagr = growth ** (1 / yrs) - 1 if yrs > 0 else 0.0
    curve = np.concatenate([[1.0], np.cumprod(1 + r)])
    dd = float((curve / np.maximum.accumulate(curve) - 1).min())
    sharpe = float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0
    return dict(cagr=cagr, dd=dd, sharpe=sharpe, mar=cagr / abs(dd) if dd < 0 else float("inf"), growth=growth)


def losing_years(days, eq):
    years = sorted({datetime.fromtimestamp(int(x), UTC).year for x in days})
    out = []
    for y in years:
        sel = (days >= ts(y)) & (days < ts(y + 1))
        if sel.sum() > 30:
            e = eq[sel]
            out.append((y, e[-1] / e[0] - 1))
    return out


lines = []


def say(s=""):
    print(s)
    lines.append(s)


say(__doc__.split("PRE-REGISTRATION")[1].split('"""')[0].rstrip())
say("=" * 110)
res = {}
for kind in ("binance", "exness"):
    data = load(kind)
    for name, cfg in VARIANTS.items():
        days, eq, trades, n = simulate(data, cfg, spot=(kind == "binance"))
        res[(kind, name)] = dict(days=days, eq=eq, trades=trades, n=n)

# sanity checks
say("\nSANITY CHECKS")
bin_data = load("binance")
for c in COINS:
    ref = [(t["t0"], t["t1"]) for t in reference_trades(bin_data[c], 0.0, 0.0)]
    mine = sorted((t["t0"], t["t1"]) for t in res[("binance", "B0 baseline (bot today)")]["trades"] if t["coin"] == c)
    ok = mine[:len(ref)] == ref
    say(f"  B0 on Binance {c}: same trades as the tested study code: {ok} ({len(ref)} trades)")
b = res[("exness", "B0 baseline (bot today)")]
s = stats(b["days"], b["eq"])
say(f"  B0 on Exness (CFD costs): {100 * s['cagr']:+.1f}%/yr, worst drop {100 * s['dd']:.1f}% "
    f"(earlier study of the same rules: about +21.4%/yr, -18.4%)")

say("\nRESULTS — main data: Binance spot, $200 from 01/03/2018, 0.1% fee each way, no leverage")
say(f"  {'version':34s} {'$200 ->':>9s} {'per yr':>7s} {'worst':>7s} {'Sharpe':>7s} {'MAR':>6s} "
    f"{'Sh 18-21':>8s} {'Sh 22-26':>8s} {'Sh ex21':>8s} {'Exness Sh':>9s} {'entries':>8s} {'adds':>5s}")
base = None
table = {}
for name in VARIANTS:
    r = res[("binance", name)]
    full = stats(r["days"], r["eq"])
    h1 = stats(r["days"], r["eq"], hi=HALF)
    h2 = stats(r["days"], r["eq"], lo=HALF)
    ex = stats(r["days"], r["eq"], skip=Y2021)
    xs = stats(res[("exness", name)]["days"], res[("exness", name)]["eq"])
    table[name] = dict(full=full, h1=h1, h2=h2, ex=ex, xs=xs, n=r["n"], days=r["days"], eq=r["eq"])
    say(f"  {name:34s} {CAPITAL * full['growth']:9.0f} {100 * full['cagr']:6.1f}% {100 * full['dd']:6.1f}% "
        f"{full['sharpe']:7.2f} {full['mar']:6.2f} {h1['sharpe']:8.2f} {h2['sharpe']:8.2f} {ex['sharpe']:8.2f} "
        f"{xs['sharpe']:9.2f} {r['n']['entries']:8d} {r['n']['adds']:5d}")
say("  (Sh = Sharpe ratio: return per unit of day-to-day swings; MAR = yearly return / worst drop)")

say("\nCRITERIA (written before the run)")
B = table["B0 baseline (bot today)"]
for name in list(VARIANTS)[1:]:
    V = table[name]
    c1 = V["full"]["mar"] >= 1.10 * B["full"]["mar"]
    c2 = V["h1"]["sharpe"] >= B["h1"]["sharpe"] and V["h2"]["sharpe"] >= B["h2"]["sharpe"]
    c3 = V["ex"]["sharpe"] >= B["ex"]["sharpe"]
    c4 = V["xs"]["sharpe"] >= B["xs"]["sharpe"]
    c5 = V["full"]["dd"] >= B["full"]["dd"] - 0.03
    verdict = "PASS" if all((c1, c2, c3, c4, c5)) else "FAIL"
    say(f"  {name:34s} C1 MAR+10%: {'yes' if c1 else 'NO '}  C2 both halves: {'yes' if c2 else 'NO '}  "
        f"C3 ex-2021: {'yes' if c3 else 'NO '}  C4 Exness: {'yes' if c4 else 'NO '}  C5 drop: {'yes' if c5 else 'NO '}"
        f"   -> {verdict}")

say("\nYEARS (main data)")
for name in VARIANTS:
    yrs = losing_years(table[name]["days"], table[name]["eq"])
    say(f"  {name:34s} " + "  ".join(f"{y}:{100 * r:+.0f}%" for y, r in yrs))
say("\nCONSTRAINTS hit (main data, $200 spot): " + "; ".join(
    f"{name.split()[0]} cap-skips {table[name]['n']['cap_skip']}, <$5 skips {table[name]['n']['small_skip']}, "
    f"cut to cash {table[name]['n']['cash_cut']}" for name in VARIANTS))
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
print(f"\nsaved {OUT}")
