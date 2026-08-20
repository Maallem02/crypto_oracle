"""
Fast reversal-detection study (2026-08-03)
==========================================
On 08-03 the engine sold BTC five times while it rallied +2.04%. Every signal
logged "Structure bearish" and "No fresh CHoCH" — the slow detectors never
flipped, and the fast one (8-candle momentum vs 1.2xATR) never triggered
because a steady grind does not move 1.2xATR in 8 candles.

This measures candidate detectors on real signals: for each, compare the
expectancy of trades it would ALLOW versus those it would BLOCK. A good
detector blocks trades with strongly negative expectancy while leaving the
good ones alone — blocking indiscriminately is easy and worthless.

Run:  python reversal_detector_study.py
"""
import sqlite3, warnings
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

SYMS   = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
FROM   = pd.Timestamp("2026-01-09")
TO     = pd.Timestamp("2026-07-24")
WARM   = 260


def atr_series(h, l, c, period=14):
    prev = np.roll(c, 1); prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).rolling(period).mean().bfill().values


def build(sym, msym):
    m5 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M5, FROM, TO))
    if m5.empty:
        return None
    m5["dt"] = pd.to_datetime(m5["time"], unit="s")
    c, h, l = m5.close.values, m5.high.values, m5.low.values
    a = atr_series(h, l, c)
    s = pd.Series(c)
    return {
        "dt": m5.dt.values, "pos": {t: i for i, t in enumerate(m5.dt.values)},
        "c": c, "h": h, "l": l, "atr": a,
        "ema20": s.ewm(span=20, adjust=False).mean().values,
        "ema50": s.ewm(span=50, adjust=False).mean().values,
        "hh20": pd.Series(h).rolling(20).max().shift(1).values,
        "ll20": pd.Series(l).rolling(20).min().shift(1).values,
    }


def detectors(d, i, bias):
    """True = this detector says REVERSAL AGAINST the signal -> block it."""
    c, a = d["c"], d["atr"][i]
    if a <= 0:
        return {}
    def mom(n):
        return (c[i] - c[i - n]) / a
    up = bias == "sell"          # a sell is threatened by upward momentum
    def against(v, thr):
        return v > thr if up else v < -thr
    return {
        "current: mom8 > 1.2ATR":      against(mom(8),  1.2),
        "mom24 > 1.5ATR":              against(mom(24), 1.5),
        "mom48 > 2.0ATR":              against(mom(48), 2.0),
        "mom48 > 1.0ATR":              against(mom(48), 1.0),
        "mom96 > 2.0ATR":              against(mom(96), 2.0),
        "price vs EMA20":              (c[i] > d["ema20"][i]) if up else (c[i] < d["ema20"][i]),
        "price vs EMA50":              (c[i] > d["ema50"][i]) if up else (c[i] < d["ema50"][i]),
        "close beyond 20-bar extreme": (c[i] > d["hh20"][i]) if up else (c[i] < d["ll20"][i]),
    }


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit("mt5 attach failed")
    gates = pd.read_csv("gate_study_rows.csv")
    gates["ts"] = pd.to_datetime(gates.ts)
    gates = gates[gates.b_ok]

    conn = sqlite3.connect("offline_dataset.sqlite")
    rows = []
    for sym, msym in SYMS.items():
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,r_outcome FROM samples WHERE symbol=? AND should_scalp=1 "
            "AND r_outcome IS NOT NULL ORDER BY ts", conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])
        s = s.merge(gates[["ts", "symbol", "bias"]], on=["ts", "symbol", "bias"])
        d = build(sym, msym)
        if d is None:
            continue
        for ts, bias, r in zip(s.ts.values, s.bias.values, s.r_outcome.values):
            i = d["pos"].get(ts)
            if i is None or i < WARM:
                continue
            det = detectors(d, i, bias)
            if not det:
                continue
            rows.append({"symbol": sym, "bias": bias, "r": float(r), **det})
    conn.close(); mt5.shutdown()

    df = pd.DataFrame(rows)
    base = df.r.mean()
    print(f"signals: {len(df):,}   baseline avg_R = {base:+.4f}\n")
    names = [c for c in df.columns if c not in ("symbol", "bias", "r")]

    print(f"  {'detector':30s} {'blocks':>7} {'%':>6} {'BLOCKED avg_R':>14} "
          f"{'KEPT avg_R':>11} {'lift':>8}")
    print("  " + "-" * 82)
    res = []
    for n in names:
        b = df[df[n]]; k = df[~df[n]]
        if len(b) < 50 or len(k) < 50:
            continue
        lift = k.r.mean() - base
        res.append((lift, n, len(b), b.r.mean(), k.r.mean()))
        print(f"  {n:30s} {len(b):>7,} {len(b)/len(df)*100:>5.1f}% "
              f"{b.r.mean():>+14.4f} {k.r.mean():>+11.4f} {lift:>+8.4f}")
    print("  " + "-" * 82)
    res.sort(reverse=True)
    print(f"  BEST by kept-expectancy: {res[0][1]}  (blocked trades avg {res[0][3]:+.4f}R)")

    print("\n=== SELL signals only (the 08-03 failure mode) ===")
    sd = df[df.bias == "sell"]; sb = sd.r.mean()
    print(f"  sells: {len(sd):,}  baseline {sb:+.4f}")
    for n in names:
        b = sd[sd[n]]; k = sd[~sd[n]]
        if len(b) < 30 or len(k) < 30:
            continue
        print(f"  {n:30s} blocks {len(b):>5,} @ {b.r.mean():>+7.4f}R  "
              f"-> kept {k.r.mean():>+7.4f}R  ({k.r.mean()-sb:+.4f})")


if __name__ == "__main__":
    main()
