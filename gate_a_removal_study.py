"""
Gate A removal study (2026-08-01)
=================================
Question: removing Gate A (the symbol-level 5m+1h structure consensus) leaves
Gate B (per-timeframe EMA20-slope filter: M5->H1) as the only trend gate.
Does that help or hurt expectancy in R?

Method: replay the 37,851 engine-approved 5m signals in offline_dataset.sqlite
(which already carry r_outcome in R units from a fixed 2.5R bracket) and
reconstruct, at each signal's timestamp, whether Gate A and Gate B would have
passed it. Then compare expectancy across the resulting cohorts.

No-lookahead rules:
  - Swing points use a CENTERED window, so a swing at bar j is only knowable
    at bar j+lookback. Only swings with j <= i-lookback are used at signal i.
  - Higher-timeframe reads use the last CLOSED H1 bar. The live bot also sees
    the forming H1 bar; using the closed one is strictly more conservative
    (it lags by up to 59 min) and cannot leak future information.
"""
import sqlite3, warnings
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

DB      = "offline_dataset.sqlite"
SYMS    = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
LOOKBACK = 5           # detect_swings lookback
WINDOW   = 250         # bars get_htf_trend fetches
FROM, TO = pd.Timestamp("2026-01-09"), pd.Timestamp("2026-07-24")


# ── helpers mirroring the bot ────────────────────────────────────────────────
def swing_mask(high, low, lookback=LOOKBACK):
    """Same rule as features/smc/structure.detect_swings (vectorised)."""
    w = lookback * 2 + 1
    sh = high >= pd.Series(high).rolling(w, center=True).max().values
    sl = low  <= pd.Series(low ).rolling(w, center=True).min().values
    return np.nan_to_num(sh, nan=0).astype(bool), np.nan_to_num(sl, nan=0).astype(bool)


def structure_trend_at(sh_idx, sl_idx, high, low, i, lookback=LOOKBACK, window=WINDOW):
    """detect_market_structure's trend, using only bars knowable at index i."""
    hi_cut = i - lookback                       # centred swings need lookback bars after
    lo_bnd = i - window
    hs = sh_idx[(sh_idx <= hi_cut) & (sh_idx >= lo_bnd)]
    ls = sl_idx[(sl_idx <= hi_cut) & (sl_idx >= lo_bnd)]
    if len(hs) < 2 or len(ls) < 2:
        return "neutral"
    h1, h2 = high[hs[-1]], high[hs[-2]]
    l1, l2 = low[ls[-1]],  low[ls[-2]]
    if h1 > h2 and l1 > l2: return "bullish"
    if h1 < h2 and l1 < l2: return "bearish"
    if h1 < h2 and l1 > l2: return "bullish"    # CHoCH bearish_to_bullish
    if h1 > h2 and l1 < l2: return "bearish"    # CHoCH bullish_to_bearish
    return "neutral"


def ema_slope_trend(close, ema20, i):
    """features/trading/router._ema_macro_trend, evaluated at bar i."""
    if i < 3 or np.isnan(ema20[i]) or np.isnan(ema20[i - 2]):
        return "neutral"
    c, e, ep = close[i], ema20[i], ema20[i - 2]
    if c > e and e > ep: return "bullish"
    if c < e and e < ep: return "bearish"
    return "neutral"


def gate_a_allows(consensus, h1_trend, ltf_trend, bias):
    """Faithful replay of the OLD Gate A, including 1H-master arbitration."""
    if consensus == "conflict":
        if ltf_trend == "neutral" and h1_trend in ("bullish", "bearish"):
            consensus = h1_trend                       # LTF calm -> 1H decides
        elif h1_trend in ("bullish", "bearish"):
            return bias == ("buy" if h1_trend == "bullish" else "sell")   # 1H master
        else:
            return False                               # hard block
    if consensus == "bearish": return bias != "buy"
    if consensus == "bullish": return bias != "sell"
    return True                                        # neutral -> no filter


def expectancy(rs):
    rs = np.asarray(rs, dtype=float)
    if len(rs) == 0:
        return dict(n=0, wr=float("nan"), avg_r=float("nan"), total_r=0.0)
    return dict(n=len(rs), wr=float((rs > 0).mean() * 100),
                avg_r=float(rs.mean()), total_r=float(rs.sum()))


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    if not mt5.initialize(timeout=20000):        # no credentials -> cannot re-login
        raise SystemExit(f"mt5 attach failed: {mt5.last_error()}")

    conn = sqlite3.connect(DB)
    rows = []
    for sym in SYMS:
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,r_outcome,score FROM samples "
            "WHERE symbol=? AND should_scalp=1 AND r_outcome IS NOT NULL ORDER BY ts",
            conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])

        m5 = pd.DataFrame(mt5.copy_rates_range(SYMS[sym], mt5.TIMEFRAME_M5, FROM, TO))
        h1 = pd.DataFrame(mt5.copy_rates_range(SYMS[sym], mt5.TIMEFRAME_H1, FROM, TO))
        m5["dt"] = pd.to_datetime(m5["time"], unit="s")
        h1["dt"] = pd.to_datetime(h1["time"], unit="s")

        m5_pos = {t: i for i, t in enumerate(m5.dt.values)}
        m5_sh, m5_sl = swing_mask(m5.high.values, m5.low.values)
        m5_sh_i, m5_sl_i = np.flatnonzero(m5_sh), np.flatnonzero(m5_sl)

        h1_sh, h1_sl = swing_mask(h1.high.values, h1.low.values)
        h1_sh_i, h1_sl_i = np.flatnonzero(h1_sh), np.flatnonzero(h1_sl)
        h1_close = h1.close.values
        h1_ema20 = pd.Series(h1_close).ewm(span=20).mean().values
        h1_e50   = pd.Series(h1_close).ewm(span=50,  adjust=False).mean().values
        h1_e200  = pd.Series(h1_close).ewm(span=200, adjust=False).mean().values
        h1_dt    = h1.dt.values

        # precompute per-H1-bar trends (cheap: a few thousand bars)
        h1_struct = [structure_trend_at(h1_sh_i, h1_sl_i, h1.high.values, h1.low.values, i)
                     for i in range(len(h1))]
        h1_emat   = [ema_slope_trend(h1_close, h1_ema20, i) for i in range(len(h1))]

        for ts, bias, r, score in zip(s.ts.values, s.bias.values,
                                      s.r_outcome.values, s.score.values):
            i5 = m5_pos.get(ts)
            if i5 is None or i5 < WINDOW:
                continue
            # last CLOSED H1 bar strictly before this 5m timestamp
            j = int(np.searchsorted(h1_dt, ts, side="right")) - 1
            if j < 200:
                continue

            ltf_trend = structure_trend_at(m5_sh_i, m5_sl_i,
                                           m5.high.values, m5.low.values, i5)
            htf_trend = h1_struct[j]

            non_neutral = [t for t in (ltf_trend, htf_trend) if t != "neutral"]
            if not non_neutral:                                  # EMA50/200 tiebreak
                if   h1_e50[j] > h1_e200[j] * 1.001: consensus = "bullish"
                elif h1_e200[j] > h1_e50[j] * 1.001: consensus = "bearish"
                else:                                consensus = "neutral"
            elif all(t == "bullish" for t in non_neutral): consensus = "bullish"
            elif all(t == "bearish" for t in non_neutral): consensus = "bearish"
            else:                                          consensus = "conflict"

            a_ok = gate_a_allows(consensus, htf_trend, ltf_trend, bias)
            b_ok = h1_emat[j] == ("bullish" if bias == "buy" else "bearish")
            rows.append(dict(ts=str(pd.Timestamp(ts)), symbol=sym, bias=bias,
                             r=float(r), score=float(score),
                             consensus=consensus, a_ok=a_ok, b_ok=b_ok))
    conn.close()
    mt5.shutdown()

    df = pd.DataFrame(rows)
    print(f"signals replayed: {len(df):,}\n")

    cohorts = {
        "no gate at all (engine only)":        df,
        "OLD live: Gate A AND Gate B":         df[df.a_ok & df.b_ok],
        "NEW live: Gate B only":               df[df.b_ok],
        "Gate A only (for reference)":         df[df.a_ok],
        ">>> DELTA: newly allowed (B, not A)": df[df.b_ok & ~df.a_ok],
    }
    print(f"{'cohort':38s} {'n':>7} {'WR%':>7} {'avg R':>8} {'total R':>10}")
    print("-" * 74)
    for name, sub in cohorts.items():
        e = expectancy(sub.r.values)
        print(f"{name:38s} {e['n']:>7,} {e['wr']:>6.1f}% {e['avg_r']:>+8.4f} {e['total_r']:>+10.1f}")

    df.to_csv("gate_study_rows.csv", index=False)
    print("\n=== PER SYMBOL: old regime vs new regime (avg R) ===")
    print(f"  {'symbol':8s} {'OLD  A and B':>22} {'NEW  B only':>22} {'delta':>9}")
    for sym in SYMS:
        g = df[df.symbol == sym]
        o = expectancy(g[g.a_ok & g.b_ok].r.values)
        n = expectancy(g[g.b_ok].r.values)
        print(f"  {sym:8s}  n={o['n']:>5,} {o['avg_r']:>+8.4f} ({o['total_r']:>+7.1f})"
              f"  n={n['n']:>5,} {n['avg_r']:>+8.4f} ({n['total_r']:>+7.1f}) {n['avg_r']-o['avg_r']:>+9.4f}")

    print("\n=== NEW regime totals with each symbol dropped ===")
    keep_all = df[df.b_ok]
    base = expectancy(keep_all.r.values)
    print(f"  {'all 4 symbols':22s} n={base['n']:>6,} avg_R={base['avg_r']:+.4f} total={base['total_r']:+8.1f}")
    for sym in SYMS:
        e = expectancy(keep_all[keep_all.symbol != sym].r.values)
        print(f"  {'drop ' + sym:22s} n={e['n']:>6,} avg_R={e['avg_r']:+.4f} total={e['total_r']:+8.1f}"
              f"  ({e['avg_r']-base['avg_r']:+.4f})")

    print("\n=== the newly-allowed trades, by symbol ===")
    d = df[df.b_ok & ~df.a_ok]
    for sym in SYMS:
        e = expectancy(d[d.symbol == sym].r.values)
        if e["n"]:
            print(f"  {sym:8s} n={e['n']:>5,}  WR={e['wr']:5.1f}%  avg_R={e['avg_r']:+.4f}  total={e['total_r']:+.1f}")

    print("\n=== newly-allowed, split by why Gate A blocked them ===")
    for cons in ("conflict", "bullish", "bearish", "neutral"):
        e = expectancy(d[d.consensus == cons].r.values)
        if e["n"]:
            print(f"  consensus={cons:9s} n={e['n']:>5,}  WR={e['wr']:5.1f}%  avg_R={e['avg_r']:+.4f}  total={e['total_r']:+.1f}")

    print("\n=== would a score floor rescue the newly-allowed set? ===")
    for thr in (60, 70, 80, 90, 100, 110):
        e = expectancy(d[d.score >= thr].r.values)
        if e["n"]:
            print(f"  score>={thr:3d}  n={e['n']:>5,}  WR={e['wr']:5.1f}%  avg_R={e['avg_r']:+.4f}  total={e['total_r']:+.1f}")


if __name__ == "__main__":
    main()
