"""MULTI-MARKET DAILY TREND TEST. Read-only (daily candles saved by
multi_market_load.py; MT5 only asked for symbol info: point, swap).

Rules = the Daily Trend bot, unchanged: Donchian 20/10, entry at the next day's
open, stop 2 x ATR(14), exit on a close below the 10-day low. Main version
long-only (as the bot trades); long+short shown second.
Costs per market: Exness spread (median daily-bar spread of the last year) paid
on entry for longs / on exit for shorts, Exness swap per calendar night when it
is a charge (credits ignored).
Portfolio: each trade risks 1 % of the account at entry (0.5 % shown too),
at most 10 open trades (10 % open risk; a signal beyond that is skipped),
account marked to market every day.
Portfolios: P0 BTC+ETH (the bot today), P1 every loaded market, P2 every
market except crypto, and each asset class.
PASS (fixed before running) = (1) P1 positive in both halves (2018-2021,
2022 -> now) AND (2) P2 positive over the whole period AND (3) P1 Sharpe >= P0
Sharpe since 2022 AND P1 max drawdown <= P0 max drawdown over the whole period."""
import ast
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import MetaTrader5 as mt5
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
D1 = os.path.join(HERE, "data", "multi_d1")
CLASS = {"US500m": "Indices", "USTECm": "Indices", "US30m": "Indices", "DE30m": "Indices", "UK100m": "Indices",
         "JP225m": "Indices", "AUS200m": "Indices", "HK50m": "Indices", "FR40m": "Indices", "STOXX50m": "Indices",
         "USOILm": "Energy", "UKOILm": "Energy", "XNGUSDm": "Energy",
         "XAUUSDm": "Metals", "XAGUSDm": "Metals", "XPTUSDm": "Metals", "XPDUSDm": "Metals", "XCUUSDm": "Metals",
         "EURUSDm": "FX", "GBPUSDm": "FX", "USDJPYm": "FX", "AUDUSDm": "FX", "USDCADm": "FX", "USDCHFm": "FX",
         "NZDUSDm": "FX", "BTCUSDm": "Crypto", "ETHUSDm": "Crypto"}
START = datetime(2018, 3, 1, tzinfo=UTC).timestamp()
HALF = datetime(2022, 1, 1, tzinfo=UTC).timestamp()
MAX_OPEN = 10


def trades_for(d, sp, sw_long, sw_short, long_only):
    """the bot's rules, entry at the next day's open; returns per-trade index ranges + prices"""
    o, h, l, c = (d[k].astype(float) for k in ("open", "high", "low", "close"))
    atr = atr_series(d)
    out, pos, i = [], None, 21
    while i < len(c) - 1:
        if pos is None:
            hi, lo = h[i - 20:i].max(), l[i - 20:i].min()
            side = 1 if c[i] > hi else (-1 if (c[i] < lo and not long_only) else 0)
            if side:
                px = o[i + 1] * (1 + sp) if side == 1 else o[i + 1]
                dist = 2.0 * atr[i]
                pos = dict(side=side, ie=i + 1, entry=px, stop=px - side * dist, dist=dist)
                i += 1
                continue
            i += 1
            continue
        s, ex = pos["side"], None
        if (s == 1 and l[i] <= pos["stop"]) or (s == -1 and h[i] >= pos["stop"]):
            ex = pos["stop"] if i == pos["ie"] else (min(pos["stop"], o[i]) if s == 1 else max(pos["stop"], o[i]))
        elif i > pos["ie"] and ((s == 1 and c[i] < l[i - 10:i].min()) or (s == -1 and c[i] > h[i - 10:i].max())):
            ex = c[i]
        if ex is not None:
            if s == -1:
                ex *= (1 + sp)
            pos.update(ix=i, exit=ex, sw=(sw_long if s == 1 else sw_short))
            out.append(pos)
            pos = None
        i += 1
    for t in out:
        nights = (d["time"][t["ix"]] - d["time"][t["ie"]]) / 86400
        pnl = t["side"] * (t["exit"] - t["entry"]) - t["sw"] * nights * t["entry"]
        t["R"] = pnl / t["dist"]
        t["t0"], t["t1"] = int(d["time"][t["ie"]]), int(d["time"][t["ix"]])
    return out


def portfolio(markets, data, trades, risk=0.01):
    """daily mark-to-market of all trades of the chosen markets"""
    days = sorted({int(t) // 86400 for m in markets for t in data[m]["time"]})
    days = [x for x in days if x * 86400 >= START]
    idx = {m: {int(t) // 86400: k for k, t in enumerate(data[m]["time"])} for m in markets}
    entries = {}
    for m in markets:
        for t in trades[m]:
            if t["t0"] >= START:
                entries.setdefault(t["t0"] // 86400, []).append((m, t))
    eq, curve, open_, skipped, taken = 1.0, [], [], 0, 0
    for day in days:
        prev_eq = eq
        for m, t in entries.get(day, []):
            if len(open_) >= MAX_OPEN:
                skipped += 1
                continue
            taken += 1
            open_.append(dict(m=m, t=t, units=risk * prev_eq / t["dist"], last=t["entry"]))
        pnl, still = 0.0, []
        for p in open_:
            k = idx[p["m"]].get(day)
            if k is None:
                still.append(p)
                continue
            t, d = p["t"], data[p["m"]]
            px = t["exit"] if k == t["ix"] else float(d["close"][k])
            pnl += t["side"] * (px - p["last"]) * p["units"] - t["sw"] * float(d["close"][k]) * p["units"]
            p["last"] = px
            if k < t["ix"]:
                still.append(p)
        open_ = still
        eq += pnl
        curve.append((day, eq))
    days_a = np.array([c[0] for c in curve])
    eq_a = np.array([c[1] for c in curve])
    return days_a, eq_a, taken, skipped


def stats(days_a, eq_a, since=None, until=None):
    sel = np.ones(len(days_a), bool)
    if since is not None:
        sel &= days_a * 86400 >= since
    if until is not None:
        sel &= days_a * 86400 < until
    e = eq_a[sel]
    if len(e) < 30:
        return None
    r = np.diff(e) / e[:-1]
    yrs = len(e) / 365
    return dict(ret=100 * (e[-1] / e[0] - 1), cagr=100 * ((e[-1] / e[0]) ** (1 / yrs) - 1),
                dd=100 * (e / np.maximum.accumulate(e) - 1).min(),
                sharpe=float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0)


def years_table(days_a, eq_a):
    out = {}
    for y in range(2018, 2027):
        s = stats(days_a, eq_a, datetime(y, 1, 1, tzinfo=UTC).timestamp(), datetime(y + 1, 1, 1, tzinfo=UTC).timestamp())
        if s:
            out[y] = s["ret"]
    return out


# ── data and costs ───────────────────────────────────────────────────────────
data = {f[:-4]: np.load(os.path.join(D1, f)) for f in sorted(os.listdir(D1)) if f.endswith(".npy")}
mt5.initialize(timeout=10000)
cost = {}
for m, d in data.items():
    si = mt5.symbol_info(m)
    last = d[-365:]
    sp = float(np.median(last["spread"]) * si.point / np.median(last["close"]))
    px = float(d["close"][-1])
    swl = min(0.0, si.swap_long * si.point / px) if si.swap_mode == 1 else 0.0
    sws = min(0.0, si.swap_short * si.point / px) if si.swap_mode == 1 else 0.0
    cost[m] = (sp, -swl, -sws)
mt5.shutdown()

# sanity check: same trades as the tested study code on BTC/ETH (long-only, next-day open)
src = open(os.path.join(HERE, "trend_robustness_study.py"), encoding="utf-8").read()
fn = next(nd for nd in ast.parse(src).body if isinstance(nd, ast.FunctionDef) and nd.name == "donchian")
ns = {"np": np, "atr_series": atr_series}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "donchian", "exec"), ns)
for m in ("BTCUSDm", "ETHUSDm"):
    if m in data:
        a = [(t["t0"], t["t1"]) for t in ns["donchian"](data[m], (0, 0), long_only=True, entry="next_open")]
        b = [(t["t0"], t["t1"]) for t in trades_for(data[m], 0, 0, 0, True)]
        print(f"sanity {m}: study {len(a)} trades, this code {len(b)}, identical: {a == b}")

print(f"\n{len(data)} markets loaded: " + ", ".join(f"{m[:-1]}({CLASS[m]})" for m in data))
print("\nCOSTS used (Exness): spread % of price / swap charged per night on longs / on shorts")
for m in data:
    sp, sl, ss = cost[m]
    print(f"  {m[:-1]:8s} {100 * sp:6.3f}%  {100 * sl:7.4f}%  {100 * ss:7.4f}%   data from "
          f"{datetime.fromtimestamp(int(data[m]['time'][0]), UTC):%m/%Y}")

for long_only in (True, False):
    label = "BUYS ONLY (as the bot trades)" if long_only else "BUYS AND SELLS"
    print("\n" + "=" * 112 + f"\n{label}\n" + "=" * 112)
    trades = {m: trades_for(data[m], *cost[m], long_only) for m in data}
    print("  per market (information only, NOT used to choose markets): trades, avg R, total R")
    for cls in ("Indices", "Energy", "Metals", "FX", "Crypto"):
        ms = [m for m in data if CLASS[m] == cls]
        cells = []
        for m in ms:
            R = [t["R"] for t in trades[m] if t["t0"] >= START]
            cells.append(f"{m[:-1]} {len(R)}/{np.mean(R) if R else 0:+.2f}/{sum(R):+.0f}")
        if ms:
            print(f"    {cls:8s} " + "  ".join(cells))
    groups = {"P0 BTC+ETH (the bot today)": [m for m in data if CLASS[m] == "Crypto"],
              "P1 ALL markets": list(data),
              "P2 all except crypto": [m for m in data if CLASS[m] != "Crypto"]}
    for cls in ("Indices", "Energy", "Metals", "FX"):
        ms = [m for m in data if CLASS[m] == cls]
        if ms:
            groups[f"   class: {cls}"] = ms
    res = {}
    for risk in (0.01, 0.005):
        print(f"\n  each trade risks {100 * risk:.1f}% | max {MAX_OPEN} open trades")
        print(f"    {'':30s} {'whole period':>38s}  {'2018-2021':>10s} {'2022->':>9s}  {'Sharpe 2022->':>13s}  "
              f"{'losing yrs':>10s} {'skipped':>8s}")
        for name, ms in groups.items():
            if not ms:
                continue
            da, ea, taken, skipped = portfolio(ms, data, trades, risk)
            w = stats(da, ea)
            h1, h2 = stats(da, ea, until=HALF), stats(da, ea, since=HALF)
            yt = years_table(da, ea)
            res[(name, risk)] = (w, h1, h2)
            print(f"    {name:30s} CAGR {w['cagr']:+6.1f}% DD {w['dd']:6.1f}% Sharpe {w['sharpe']:+.2f}  "
                  f"{(h1 or {'ret': float('nan')})['ret']:+9.1f}% {h2['ret']:+8.1f}%  {h2['sharpe']:+13.2f}  "
                  f"{sum(1 for v in yt.values() if v < 0):>5d}/{len(yt):<4d} {skipped:>8d}")
        if risk == 0.01:
            da, ea, _, _ = portfolio(list(data), data, trades, risk)
            print("    P1 per year: " + "  ".join(f"{y} {v:+.1f}%" for y, v in years_table(da, ea).items()))
            da0, ea0, _, _ = portfolio(groups["P0 BTC+ETH (the bot today)"], data, trades, risk)
            print("    P0 per year: " + "  ".join(f"{y} {v:+.1f}%" for y, v in years_table(da0, ea0).items()))
    if long_only:
        w1, h1a, h1b = res[("P1 ALL markets", 0.01)]
        w0, _, h0b = res[("P0 BTC+ETH (the bot today)", 0.01)]
        w2, _, _ = res[("P2 all except crypto", 0.01)]
        checks = {"(1) P1 positive in both halves": h1a["ret"] > 0 and h1b["ret"] > 0,
                  "(2) P2 (no crypto) positive overall": w2["ret"] > 0,
                  "(3a) P1 Sharpe >= P0 since 2022": h1b["sharpe"] >= h0b["sharpe"],
                  "(3b) P1 max drawdown <= P0": w1["dd"] >= w0["dd"]}
        print("\n  PRE-REGISTERED CHECKS (buys only, 1% risk): " + " | ".join(f"{k}: {'yes' if v else 'NO'}" for k, v in checks.items()))
        print(f"  VERDICT: {'PASS' if all(checks.values()) else 'FAIL'}")
