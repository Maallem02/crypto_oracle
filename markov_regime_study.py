"""THE VIDEO'S "HEDGE FUND METHOD" (Markov regime model), tested. Read-only.

Rules exactly as described in the video:
  * state of day t from the last 20 days' return: >= +5 % bull, <= -5 % bear, else sideways
  * transition matrix = counts of state(t-1) -> state(t), turned into row %
  * signal = P(bull tomorrow) - P(bear tomorrow) from the row of today's state;
    the sign gives the direction, the size gives the position (-1 .. +1)
  * walk-forward: the matrix at day t only uses transitions seen up to day t
  * hidden Markov model (3 Gaussian states on daily returns), refitted every
    year on all data before that year, then run forward (filtered, no
    look-ahead) through the year; "confirmed" = both signals agree
Checks fixed before running:
  A. is the "stickiness" a market property? -> same matrix on SHUFFLED daily
     returns (no memory, same volatility), 200 shuffles
  B. does P(bull)-P(bear) predict the NEXT DAY's return better than the
     20-day return itself?
  C. trading results, costs included (Exness spread, swap on longs), vs buy &
     hold, vs the plain 20-day momentum rule the signal reduces to, vs the
     Daily Trend system. PASS = in the test years (2023 -> now), on BTC AND
     ETH: Sharpe > 0.5, above buy & hold AND above plain 20-day momentum.
Data: Exness daily, BTC/ETH from 2018, XAU/USDJPY from 2014."""
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
COST = {"BTC": (0.000119, 0.000219, 365), "ETH": (0.00037, 0.000219, 365),
        "XAU": (0.000057, 0.000126, 260), "JPY": (0.000063, 0.0, 260)}
TEST0 = datetime(2023, 1, 1, tzinfo=UTC).timestamp()
rng = np.random.default_rng(42)
BEAR, SIDE, BULL = 0, 1, 2


def states(c, n=20, th=0.05):
    r20 = np.full(len(c), np.nan)
    r20[n:] = c[n:] / c[:-n] - 1
    s = np.full(len(c), -1)
    s[n:] = np.where(r20[n:] >= th, BULL, np.where(r20[n:] <= -th, BEAR, SIDE))
    return s, r20


def full_matrix(s):
    m = np.zeros((3, 3))
    for a, b in zip(s[:-1], s[1:]):
        if a >= 0 and b >= 0:
            m[a, b] += 1
    return m / np.maximum(m.sum(1, keepdims=True), 1)


def markov_signal(s, warm=250):
    """walk-forward: signal[t] uses transitions up to day t only"""
    cnt = np.zeros((3, 3))
    sig = np.full(len(s), np.nan)
    for t in range(1, len(s)):
        if s[t - 1] >= 0 and s[t] >= 0:
            cnt[s[t - 1], s[t]] += 1
        if t >= warm and s[t] >= 0 and cnt[s[t]].sum() >= 10:
            row = cnt[s[t]] / cnt[s[t]].sum()
            sig[t] = row[BULL] - row[BEAR]
    return sig


# ── a small Gaussian HMM (Baum-Welch with scaling, forward filtering) ───────
def hmm_fit(x, k=3, iters=60):
    q = np.quantile(x, [0.15, 0.5, 0.85])
    mu, var = q.copy(), np.full(k, x.var())
    A, pi = np.full((k, k), 0.05) + np.eye(k) * (1 - 0.05 * k), np.full(k, 1 / k)
    for _ in range(iters):
        B = np.exp(-(x[:, None] - mu) ** 2 / (2 * var)) / np.sqrt(2 * np.pi * var) + 1e-300
        T = len(x)
        al, c = np.zeros((T, k)), np.zeros(T)
        al[0] = pi * B[0]; c[0] = al[0].sum(); al[0] /= c[0]
        for t in range(1, T):
            al[t] = (al[t - 1] @ A) * B[t]; c[t] = al[t].sum(); al[t] /= c[t]
        be = np.ones((T, k))
        for t in range(T - 2, -1, -1):
            be[t] = (A @ (B[t + 1] * be[t + 1])) / c[t + 1]
        g = al * be
        g /= g.sum(1, keepdims=True)
        xi = np.zeros((k, k))
        for t in range(T - 1):
            m = al[t][:, None] * A * (B[t + 1] * be[t + 1])[None, :]
            xi += m / m.sum()
        A = xi / xi.sum(1, keepdims=True)
        pi = g[0]
        w = g.sum(0)
        mu = (g * x[:, None]).sum(0) / w
        var = np.maximum((g * (x[:, None] - mu) ** 2).sum(0) / w, 1e-10)
    return A, mu, var, pi


def hmm_signal(t_arr, r, warm_year=2020):
    """refit each Jan 1 on everything before, filter forward through the year"""
    sig = np.full(len(r), np.nan)
    years = sorted({datetime.fromtimestamp(int(t), UTC).year for t in t_arr})
    for y in years:
        if y < warm_year:
            continue
        y0 = datetime(y, 1, 1, tzinfo=UTC).timestamp()
        y1 = datetime(y + 1, 1, 1, tzinfo=UTC).timestamp()
        train = (t_arr < y0) & np.isfinite(r)
        if train.sum() < 500:
            continue
        A, mu, var, pi = hmm_fit(r[train])
        bull, bear = int(np.argmax(mu)), int(np.argmin(mu))
        a = pi.copy()
        idx = np.nonzero((t_arr >= y0) & (t_arr < y1))[0]
        # warm the filter on the last 60 training days, then run through the year
        for t in list(np.nonzero(train)[0][-60:]) + list(idx):
            if not np.isfinite(r[t]):
                continue
            b = np.exp(-(r[t] - mu) ** 2 / (2 * var)) / np.sqrt(2 * np.pi * var) + 1e-300
            a = (a @ A) * b
            a /= a.sum()
            if t in set(idx):
                nxt = a @ A
                sig[t] = nxt[bull] - nxt[bear]
    return sig


def run(pos, r, sp, sw):
    """pos[t] decided at the close of day t, earns r[t+1]"""
    p = np.nan_to_num(pos)
    ret = p[:-1] * r[1:] - np.abs(np.diff(np.r_[0.0, p]))[:-1] * sp - np.clip(p[:-1], 0, None) * sw
    return ret


def sharpe(x, py):
    x = x[np.isfinite(x)]
    return float(x.mean() / x.std() * np.sqrt(py)) if len(x) > 30 and x.std() > 0 else float("nan")


def cagr_dd(x, py):
    eq = np.cumprod(1 + x)
    yrs = len(x) / py
    return (eq[-1] ** (1 / yrs) - 1) * 100, (eq / np.maximum.accumulate(eq) - 1).min() * 100


# the Daily Trend system, from the tested study code (long-only, as built)
src = open(os.path.join(HERE, "trend_robustness_study.py"), encoding="utf-8").read()
fn = next(nd for nd in ast.parse(src).body if isinstance(nd, ast.FunctionDef) and nd.name == "donchian")
ns = {"np": np, "atr_series": atr_series}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "donchian", "exec"), ns)


def dt_daily_returns(d, cost):
    """daily P/L of the Daily Trend positions at 100% exposure, for a Sharpe comparison"""
    sp, sw, _ = cost
    c = d["close"].astype(float)
    pos = np.zeros(len(c))
    for tr in ns["donchian"](d, (sp, sw), long_only=True, entry="next_open"):
        i0 = np.searchsorted(d["time"], tr["t0"])
        i1 = np.searchsorted(d["time"], tr["t1"])
        pos[i0 - 1:i1] = 1.0
    return pos


mt5.initialize(timeout=10000)
DATA = {s: mt5.copy_rates_from_pos(s + ("USDm" if s in ("BTC", "ETH", "XAU") else "m"), mt5.TIMEFRAME_D1, 0, 5000)
        for s in ("BTC", "ETH", "XAU")}
DATA["JPY"] = mt5.copy_rates_from_pos("USDJPYm", mt5.TIMEFRAME_D1, 0, 5000)
mt5.shutdown()

print("=" * 108 + "\nA. IS THE 'STICKINESS' A MARKET PROPERTY?  (diagonal of the matrix: P(same state tomorrow))\n" + "=" * 108)
print(f"  {'':5s} {'':14s} {'bear->bear':>11s} {'side->side':>11s} {'bull->bull':>11s}")
for sym, d in DATA.items():
    c = d["close"].astype(float)
    s, _ = states(c)
    real = np.diag(full_matrix(s))
    r = c[1:] / c[:-1] - 1
    sh = []
    for _ in range(200):
        cs = c[0] * np.cumprod(np.r_[1.0, 1 + rng.permutation(r)])
        sh.append(np.diag(full_matrix(states(cs)[0])))
    sh = np.array(sh)
    print(f"  {sym:5s} {'real':14s} " + " ".join(f"{100 * v:10.1f}%" for v in real))
    print(f"  {'':5s} {'SHUFFLED':14s} " + " ".join(f"{100 * v:10.1f}%" for v in sh.mean(0))
          + "   <- returns in random order: no trend, no memory")

print("\n" + "=" * 108 + "\nB. DOES THE SIGNAL PREDICT TOMORROW'S RETURN?  (walk-forward signal vs the next day's return)\n" + "=" * 108)
for sym, d in DATA.items():
    c = d["close"].astype(float)
    s, r20 = states(c)
    sig = markov_signal(s)
    nxt = np.r_[c[1:] / c[:-1] - 1, np.nan]
    ok = np.isfinite(sig) & np.isfinite(nxt) & np.isfinite(r20)
    corr_sig = np.corrcoef(sig[ok], nxt[ok])[0, 1]
    corr_r20 = np.corrcoef(r20[ok], nxt[ok])[0, 1]
    corr_sr = np.corrcoef(sig[ok], np.sign(r20[ok]) * (np.abs(r20[ok]) >= 0.05))[0, 1]
    by = {st: 100 * np.nanmean(nxt[ok & (s == st)]) for st in (BEAR, SIDE, BULL)}
    print(f"  {sym}: corr(signal, next day) {corr_sig:+.3f} | corr(20-day return, next day) {corr_r20:+.3f} | "
          f"corr(signal, 'bull/bear label') {corr_sr:+.2f} | next-day mean in bear / side / bull: "
          f"{by[BEAR]:+.2f}% / {by[SIDE]:+.2f}% / {by[BULL]:+.2f}%")

print("\n" + "=" * 108 + "\nC. TRADING IT  (walk-forward, costs included; Sharpe, CAGR, max drawdown at 100 % exposure)\n" + "=" * 108)
verdict = {}
for sym, d in DATA.items():
    sp, sw, py = COST[sym]
    t = d["time"]
    c = d["close"].astype(float)
    r = np.r_[np.nan, c[1:] / c[:-1] - 1]
    s, r20 = states(c)
    msig = markov_signal(s)
    hsig = hmm_signal(t, np.log(c / np.r_[c[0], c[:-1]]))
    start = np.nonzero(np.isfinite(msig))[0][0]
    test = t >= TEST0
    variants = {
        "buy & hold": np.ones(len(c)),
        "plain 20-day momentum (sign)": np.sign(np.nan_to_num(r20)),
        "video: state rule (+1 bull, -1 bear, 0 side)": np.where(s == BULL, 1.0, np.where(s == BEAR, -1.0, 0.0)),
        "video: Markov signal P(bull)-P(bear)": msig,
        "video: Markov signal, long only": np.clip(np.nan_to_num(msig), 0, None),
        "video: HMM signal": hsig,
        "video: Markov + HMM confirmed": np.where(np.sign(np.nan_to_num(msig)) == np.sign(np.nan_to_num(hsig)),
                                                 np.nan_to_num(msig), 0.0),
        "Daily Trend (Donchian 20/10, long only)": dt_daily_returns(d, COST[sym]),
    }
    print(f"\n  {sym} ({datetime.fromtimestamp(int(t[start]), UTC):%Y} -> {datetime.fromtimestamp(int(t[-1]), UTC):%Y}; "
          f"test years from 2023)")
    print(f"    {'':47s} {'whole period':>32s}   {'test years 2023 ->':>30s}")
    res = {}
    for lab, pos in variants.items():
        pos = np.where(np.arange(len(c)) >= start, pos, 0.0)
        ret = run(pos, np.nan_to_num(r), sp, sw)
        whole = ret[start:]
        tt = ret[test[:-1] & (np.arange(len(ret)) >= start)]
        if lab.startswith("video: HMM") or "confirmed" in lab:
            whole = ret[np.isfinite(hsig[:-1]) | ~np.isfinite(hsig[:-1]) & False]
            whole = ret[np.nonzero(np.isfinite(hsig))[0][0]:] if np.isfinite(hsig).any() else whole
        cw, dw = cagr_dd(whole, py)
        ct, dtt = cagr_dd(tt, py)
        res[lab] = sharpe(tt, py)
        print(f"    {lab:47s} Sharpe {sharpe(whole, py):+5.2f} CAGR {cw:+6.1f}% DD {dw:6.1f}%   "
              f"Sharpe {res[lab]:+5.2f} CAGR {ct:+6.1f}% DD {dtt:6.1f}%")
    verdict[sym] = res

print("\n" + "=" * 108 + "\nPRE-REGISTERED VERDICT (BTC and ETH, test years 2023 ->)\n" + "=" * 108)
for lab in ("video: Markov signal P(bull)-P(bear)", "video: Markov + HMM confirmed"):
    ok = all(verdict[s][lab] > 0.5 and verdict[s][lab] > verdict[s]["buy & hold"]
             and verdict[s][lab] > verdict[s]["plain 20-day momentum (sign)"] for s in ("BTC", "ETH"))
    print(f"  {lab:40s} " + " | ".join(
        f"{s}: {verdict[s][lab]:+.2f} vs hold {verdict[s]['buy & hold']:+.2f} vs 20d-mom {verdict[s]['plain 20-day momentum (sign)']:+.2f}"
        for s in ("BTC", "ETH")) + f"  -> {'PASS' if ok else 'FAIL'}")
