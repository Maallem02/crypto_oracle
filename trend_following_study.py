"""DAILY TREND FOLLOWING — BTC/ETH on 3 years of Binance data, plus the same fixed
rules on Exness's longer daily history (BTC/ETH from 2018, gold/USDJPY from 2014).
Read-only. Every rule and parameter fixed before any result was seen.

1. DONCHIAN (Turtle) 20/10, long and short
   entry : daily close above the highest high of the previous 20 days -> long
           (below the lowest low -> short), at that close
   exit  : close below the lowest low of the previous 10 days (short: above the
           10-day high), or the 2 x ATR(20) protective stop intraday (gap -> open)
   R     : 1R = 2 x ATR(20) at entry
2. TIME-SERIES MOMENTUM 90 days: long if close > close 90 days ago, else short;
   sized to 20 % annual volatility (20-day realised, max 2x), changed at each close.
   Also long-only (flat instead of short).
Robustness neighbours, reported but NOT used to pick: Donchian 10/5 and 55/20,
momentum 30 and 180 days.
Costs: Exness spread on every entry/exit (BTC 0.0119 %, ETH 0.037 %, XAU 0.0057 %,
JPY 0.0063 % of price) and Exness swap on LONGS per calendar night (BTC/ETH 0.0219 %,
XAU 0.0126 %, JPY 0; shorts 0) — today's rates, applied to the past.
Benchmark: buy and hold (same spread, same swap).

WORKS (pre-registered) = Donchian 20/10: PF > 1.3 and avg R > 0 on BTC and on ETH
over the 3 Binance years AND avg R > 0 on every longer Exness history; momentum 90:
Sharpe > 0.5 on BTC and ETH over 3 years AND > 0 on every longer history."""
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import MetaTrader5 as mt5
import vp_long_history_study as L
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
COST = {"BTC": (0.000119, 0.000219), "ETH": (0.00037, 0.000219), "XAU": (0.000057, 0.000126), "JPY": (0.000063, 0.0)}
rng = np.random.default_rng(1)


def daily_binance(pair):
    d = L.resample(L.load(pair), 86400)
    return d[d["time"] >= datetime(2023, 10, 1, tzinfo=UTC).timestamp()]


def daily_exness(sym):
    return mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 5000)


def donchian(d, n_in, n_out, cost):
    sp, sw = cost
    t, o, h, l, c = (d[k].astype(float) if k != "time" else d[k] for k in ("time", "open", "high", "low", "close"))
    atr = atr_series(d)
    trades, pos, i = [], None, max(n_in, 20) + 1
    while i < len(c):
        if pos is None:
            hi, lo = h[i - n_in:i].max(), l[i - n_in:i].min()
            if c[i] > hi or c[i] < lo:
                side = 1 if c[i] > hi else -1
                entry = c[i] * (1 + sp) if side == 1 else c[i]
                pos = dict(side=side, entry=entry, stop=entry - side * 2 * atr[i], r=2 * atr[i], i0=i)
            i += 1
            continue
        s = pos["side"]
        exit_px = None
        if (s == 1 and l[i] <= pos["stop"]) or (s == -1 and h[i] >= pos["stop"]):
            exit_px = min(pos["stop"], o[i]) if s == 1 else max(pos["stop"], o[i])
        elif (s == 1 and c[i] < l[i - n_out:i].min()) or (s == -1 and c[i] > h[i - n_out:i].max()):
            exit_px = c[i]
        if exit_px is not None:
            if s == -1:
                exit_px *= (1 + sp)                          # buying back pays the spread
            nights = (t[i] - t[pos["i0"]]) / 86400
            swap = sw * nights * pos["entry"] if s == 1 else 0.0
            pnl = s * (exit_px - pos["entry"]) - swap
            trades.append(dict(side=s, t0=int(t[pos["i0"]]), t1=int(t[i]), R=pnl / pos["r"], days=nights))
            pos = None
        i += 1
    return trades


def equity(trades, t_end, risk=0.01):
    eq, curve = 1.0, [1.0]
    for tr in sorted(trades, key=lambda x: x["t1"]):
        eq *= 1 + risk * tr["R"]
        curve.append(eq)
    curve = np.array(curve)
    return eq, float((curve / np.maximum.accumulate(curve) - 1).min())


def momentum(d, look, cost, long_only=False):
    sp, sw = cost
    c = d["close"].astype(float)
    r = np.r_[0.0, c[1:] / c[:-1] - 1]
    vol = np.array([r[max(1, i - 19):i + 1].std() * np.sqrt(365) if i > 20 else np.nan for i in range(len(c))])
    w = np.zeros(len(c))
    for i in range(look, len(c)):
        s = 1.0 if c[i] > c[i - look] else (0.0 if long_only else -1.0)
        w[i] = s * min(2.0, 0.20 / vol[i]) if vol[i] > 0 else 0.0
    ret = w[:-1] * r[1:] - np.abs(np.diff(w)) * sp - np.clip(w[:-1], 0, None) * sw
    ret = ret[look:]
    return ret


def stats_ret(ret, per_year=365):
    if len(ret) == 0 or ret.std() == 0:
        return "no data"
    eq = np.cumprod(1 + ret)
    years = len(ret) / per_year
    cagr = eq[-1] ** (1 / years) - 1
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    sharpe = ret.mean() / ret.std() * np.sqrt(per_year)
    return f"Sharpe {sharpe:+.2f}  CAGR {100 * cagr:+6.1f}%  max DD {100 * dd:6.1f}%", sharpe


def buy_hold(d, cost, per_year):
    sp, sw = cost
    c = d["close"].astype(float)
    r = np.r_[c[1:] / c[:-1] - 1] - sw
    r[0] -= sp
    return stats_ret(r, per_year)


def rstats(trs):
    if not trs:
        return "no trades", float("nan"), float("nan")
    x = np.array([t["R"] for t in trs])
    gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
    pf = gp / gl if gl else float("inf")
    boot = [rng.choice(x, len(x)).mean() for _ in range(2000)]
    return (f"{len(x):3d} trades  WR {100 * (x > 0).mean():3.0f}%  avg {x.mean():+.2f}R "
            f"[90% CI {np.percentile(boot, 5):+.2f}..{np.percentile(boot, 95):+.2f}]  sum {x.sum():+6.1f}R  PF {pf:4.2f}  "
            f"avg hold {np.mean([t['days'] for t in trs]):4.0f} d"), x.mean(), pf


mt5.initialize(timeout=10000)
SETS = [("BTC", "Binance 3y", daily_binance("BTCUSDT"), 365), ("ETH", "Binance 3y", daily_binance("ETHUSDT"), 365),
        ("BTC", "Exness 2018+", daily_exness("BTCUSDm"), 365), ("ETH", "Exness 2018+", daily_exness("ETHUSDm"), 365),
        ("XAU", "Exness 2014+", daily_exness("XAUUSDm"), 260), ("JPY", "Exness 2014+", daily_exness("USDJPYm"), 260)]
mt5.shutdown()

verdict = {"don": [], "mom": []}
for sym, src, d, py in SETS:
    cost = COST[sym]
    span = f"{datetime.fromtimestamp(int(d['time'][0]), UTC):%m/%Y} -> {datetime.fromtimestamp(int(d['time'][-1]), UTC):%m/%Y}"
    print("\n" + "=" * 110 + f"\n{sym}  ({src}, {span}, {len(d)} daily bars)\n" + "=" * 110)
    bh = buy_hold(d, cost, py)
    print(f"  buy and hold                     {bh[0]}")
    for n_in, n_out in ((20, 10), (10, 5), (55, 20)):
        trs = donchian(d, n_in, n_out, cost)
        s, a, pf = rstats(trs)
        tag = "  <- pre-registered" if (n_in, n_out) == (20, 10) else "     (neighbour)"
        print(f"  Donchian {n_in}/{n_out:<3d} long+short   {s}{tag}")
        if (n_in, n_out) == (20, 10):
            for side, lab in ((1, "long only "), (-1, "short only")):
                print(f"      {lab}                 {rstats([t for t in trs if t['side'] == side])[0]}")
            eq, dd = equity(trs, d["time"][-1])
            print(f"      at 1% risk per trade: account x{eq:.2f}, max drawdown {100 * dd:.1f}%")
            yrs = {}
            for t in trs:
                yrs.setdefault(datetime.fromtimestamp(t["t1"], UTC).year, []).append(t["R"])
            print("      per year: " + "  ".join(f"{y} {sum(v):+.1f}R" for y, v in sorted(yrs.items())))
            verdict["don"].append((sym, src, a, pf))
    for look in (90, 30, 180):
        s = stats_ret(momentum(d, look, cost), py)
        tag = "  <- pre-registered" if look == 90 else "     (neighbour)"
        print(f"  momentum {look:3d}d long/short       {s[0]}{tag}")
        if look == 90:
            print(f"  momentum  90d long only        {stats_ret(momentum(d, 90, cost, long_only=True), py)[0]}")
            verdict["mom"].append((sym, src, s[1]))

print("\n" + "=" * 110 + "\nPRE-REGISTERED VERDICT\n" + "=" * 110)
b3 = [v for v in verdict["don"] if v[1] == "Binance 3y"]
lo = [v for v in verdict["don"] if v[1] != "Binance 3y"]
ok_don = all(a > 0 and pf > 1.3 for _, _, a, pf in b3) and all(a > 0 for _, _, a, _ in lo)
print("  Donchian 20/10: " + " | ".join(f"{s} {src}: avg {a:+.2f}R PF {pf:.2f}" for s, src, a, pf in verdict["don"]))
print(f"  -> {'WORKS' if ok_don else 'DOES NOT PASS'}")
m3 = [v for v in verdict["mom"] if v[1] == "Binance 3y"]
ml = [v for v in verdict["mom"] if v[1] != "Binance 3y"]
ok_mom = all(s > 0.5 for _, _, s in m3) and all(s > 0 for _, _, s in ml)
print("  Momentum 90d:  " + " | ".join(f"{s} {src}: Sharpe {sh:+.2f}" for s, src, sh in verdict["mom"]))
print(f"  -> {'WORKS' if ok_mom else 'DOES NOT PASS'}")
