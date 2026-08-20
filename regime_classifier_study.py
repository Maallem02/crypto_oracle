"""
Regime classifier study (2026-08-03)
====================================
Different question from the reversal study. That one asked "is direction about
to flip?" and every answer was worthless — the trades those detectors blocked
were the most profitable ones.

This asks instead: is there a measurable market STATE in which the
mean-reversion edge stops working? The strategy sells wick rejections into
strength. That should work in a market that oscillates and fail in one that
grinds one way relentlessly. If such a state exists and is detectable BEFORE
entry, it is worth gating on. If it does not, the answer is that the losses
are variance and only the circuit breaker helps.

Measures tested (all computed on closed bars only, at signal time):
  efficiency ratio   |net move| / sum(|bar moves|)  -> 1 = pure trend, 0 = chop
  ADX                classic trend strength
  atr_ratio          short ATR / long ATR -> volatility expansion
  persistence        share of up-closes over N bars
  extension          |close - EMA200| / ATR -> how stretched from the mean
  vol percentile     current ATR vs its own 500-bar history

Scored under the LIVE exit policy (BE 1.5R + trail 40%) with the 4H filter
applied, so results describe the bot as it actually runs.
"""
import sqlite3, warnings, importlib.util
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")
_s = importlib.util.spec_from_file_location("es", "exit_rule_study.py")
es = importlib.util.module_from_spec(_s); _s.loader.exec_module(es)

SYMS = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
FROM, TO = pd.Timestamp("2026-01-09"), pd.Timestamp("2026-07-24")
WARM = 520


def prep(msym):
    m5 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M5, FROM, TO))
    if m5.empty:
        return None
    m5["dt"] = pd.to_datetime(m5["time"], unit="s")
    c, h, l = m5.close.values, m5.high.values, m5.low.values
    prev = np.roll(c, 1); prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    atr = pd.Series(tr).rolling(14).mean().bfill().values

    absmove = pd.Series(np.abs(np.diff(c, prepend=c[0])))
    def eff(n):
        net = np.abs(c - np.roll(c, n)); net[:n] = np.nan
        tot = absmove.rolling(n).sum().values
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(tot > 0, net / tot, np.nan)

    up = pd.Series((np.diff(c, prepend=c[0]) > 0).astype(float))
    # Wilder ADX
    dfh, dfl = pd.Series(h), pd.Series(l)
    upm, dnm = dfh.diff(), -dfl.diff()
    pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0)
    mdm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    a14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean()
    pdi = 100 * pd.Series(pdm).ewm(alpha=1/14, adjust=False).mean() / a14.replace(0, np.nan)
    mdi = 100 * pd.Series(mdm).ewm(alpha=1/14, adjust=False).mean() / a14.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1/14, adjust=False).mean().values

    return {
        "pos": {t: i for i, t in enumerate(m5.dt.values)},
        "h": h, "l": l, "c": c, "atr": atr, "adx": adx,
        "er48": eff(48), "er96": eff(96),
        "persist48": up.rolling(48).mean().values,
        "ema200": pd.Series(c).ewm(span=200, adjust=False).mean().values,
        "atr_s": pd.Series(tr).rolling(7).mean().bfill().values,
        "atr_l": pd.Series(tr).rolling(50).mean().bfill().values,
        "volpct": pd.Series(atr).rolling(500).rank(pct=True).values,
    }


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit("mt5 attach failed")
    gates = pd.read_csv("gate_study_rows.csv")
    gates["ts"] = pd.to_datetime(gates.ts); gates = gates[gates.b_ok]
    conn = sqlite3.connect("offline_dataset.sqlite")

    rows = []
    for sym, msym in SYMS.items():
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,entry,risk,macro4h FROM samples WHERE symbol=? "
            "AND should_scalp=1 AND r_outcome IS NOT NULL ORDER BY ts", conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])
        s = s.merge(gates[["ts", "symbol", "bias"]], on=["ts", "symbol", "bias"])
        need = np.where(s.bias == "buy", "bullish", "bearish")
        s = s[(s.macro4h == "neutral") | (s.macro4h == need)]      # 4H filter, as live
        d = prep(msym)
        if d is None:
            continue
        for ts, bias, entry, risk in zip(s.ts.values, s.bias.values, s.entry.values, s.risk.values):
            i = d["pos"].get(ts)
            if i is None or i < WARM or risk <= 0:
                continue
            a = d["atr"][i]
            if not a or a <= 0 or np.isnan(d["er48"][i]):
                continue
            r = es.run_exit(d["h"], d["l"], i + 1, bias == "buy", float(entry), float(risk), 1.5, 0.40)
            if r is None:
                continue
            rows.append({
                "ts": pd.Timestamp(ts), "symbol": sym, "bias": bias, "r": r,
                "er48": d["er48"][i], "er96": d["er96"][i], "adx": d["adx"][i],
                "persist": d["persist48"][i],
                "extension": abs(d["c"][i] - d["ema200"][i]) / a,
                "atr_ratio": d["atr_s"][i] / d["atr_l"][i] if d["atr_l"][i] else np.nan,
                "volpct": d["volpct"][i],
            })
    conn.close(); mt5.shutdown()

    df = pd.DataFrame(rows).dropna()
    base = df.r.mean()
    te = df[df.ts >= "2026-05-01"]
    print(f"signals {len(df):,}   baseline avg_R {base:+.4f}   (test half {len(te):,})\n")

    def buckets(col, qs=(0, .2, .4, .6, .8, 1.0)):
        edges = df[col].quantile(qs).values
        print(f"=== {col} ===")
        print(f"  {'range':>22} {'n':>6} {'avg R':>9} {'sells R':>9} {'buys R':>9} {'TEST R':>9}")
        for k in range(len(edges) - 1):
            lo, hi = edges[k], edges[k + 1]
            m = (df[col] >= lo) & (df[col] <= hi if k == len(edges) - 2 else df[col] < hi)
            g = df[m]
            if len(g) < 100:
                continue
            gs = g[g.bias == "sell"]; gb = g[g.bias == "buy"]
            gt = te[(te[col] >= lo) & ((te[col] <= hi) if k == len(edges) - 2 else (te[col] < hi))]
            print(f"  {lo:>9.3f}-{hi:<11.3f} {len(g):>6,} {g.r.mean():>+9.4f} "
                  f"{(gs.r.mean() if len(gs)>30 else float('nan')):>+9.4f} "
                  f"{(gb.r.mean() if len(gb)>30 else float('nan')):>+9.4f} "
                  f"{(gt.r.mean() if len(gt)>30 else float('nan')):>+9.4f}")
        print()

    for col in ("er48", "er96", "adx", "persist", "extension", "atr_ratio", "volpct"):
        buckets(col)

    print("=== does ANY bucket lose money, consistently in both halves? ===")
    found = False
    for col in ("er48", "er96", "adx", "persist", "extension", "atr_ratio", "volpct"):
        edges = df[col].quantile((0, .2, .4, .6, .8, 1.0)).values
        for k in range(len(edges) - 1):
            lo, hi = edges[k], edges[k + 1]
            m = (df[col] >= lo) & (df[col] < hi)
            g, gt = df[m], te[(te[col] >= lo) & (te[col] < hi)]
            if len(g) < 300 or len(gt) < 150:
                continue
            if g.r.mean() < 0 and gt.r.mean() < 0:
                found = True
                print(f"  {col} in [{lo:.3f},{hi:.3f}): all {g.r.mean():+.4f} (n={len(g):,}), "
                      f"test {gt.r.mean():+.4f} (n={len(gt):,})")
    if not found:
        print("  none — no regime bucket is negative in both halves")


if __name__ == "__main__":
    main()
