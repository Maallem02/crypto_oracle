"""Exit-management study — can a dynamic exit beat the fixed 2.5R + ratchet?

Tests on the WITH-TREND (rule-approved) signals across BTC/ETH/XAU/XAG, using
each trade's real forward M5 path. Reports expectancy(R) per exit scheme,
pooled and per symbol, with a split-half robustness check (first vs second
half chronologically). Honest bar: a scheme must beat the current baseline
on BOTH halves to be worth considering.
"""
import sqlite3, sys, warnings
from datetime import timedelta
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
MT5 = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
HORIZON = 288   # 24h of M5

# exit schemes: (name, tp, be_trigger, trail_start, trail_dist, partial_at, partial_tp)
SCHEMES = [
    ("baseline TP2.5 BE@1.0",     2.5, 1.0, None, None, None, None),
    ("TP3.0 BE@1.0",              3.0, 1.0, None, None, None, None),
    ("TP3.5 BE@1.0",              3.5, 1.0, None, None, None, None),
    ("TP2.5 BE@0.5 (fast BE)",    2.5, 0.5, None, None, None, None),
    ("TP2.5 BE@1.5 (late BE)",    2.5, 1.5, None, None, None, None),
    ("TP2.5 no ratchet",          2.5, None, None, None, None, None),
    ("trail 1R after +1R",        5.0, None, 1.0, 1.0, None, None),
    ("partial 50%@1.5 rest TP3",  3.0, 1.0, None, None, 1.5, None),
]


def sim(fav, adv, tp, be, tstart, tdist, partial_at, _):
    sl = -1.0; peak = 0.0; realized = 0.0; weight = 1.0; part = False
    for f, a in zip(fav, adv):
        peak = max(peak, f)
        if be is not None and peak >= be: sl = max(sl, 0.0)
        if tstart is not None and peak >= tstart: sl = max(sl, peak - tdist)
        if -a <= sl:                              # conservative: stop first
            return realized + weight * sl
        if partial_at is not None and not part and f >= partial_at:
            realized += 0.5 * partial_at; weight = 0.5; part = True
        if f >= tp:
            return realized + weight * tp
    return realized + weight * 0.0                # unresolved -> assume flat


def paths_for(symbol):
    import MetaTrader5 as mt5
    conn = sqlite3.connect(DB)
    s = pd.read_sql_query(
        "SELECT ts,entry,risk,bias,macro4h,should_scalp FROM samples "
        "WHERE symbol=? AND label IS NOT NULL", conn, params=(symbol,))
    conn.close()
    s["ts"] = pd.to_datetime(s["ts"])
    a4 = np.zeros(len(s), int)
    a4[((s.bias == "buy") & (s.macro4h == "bullish")).values] = 1
    a4[((s.bias == "sell") & (s.macro4h == "bearish")).values] = 1
    s = s[(s.should_scalp == 1) & (a4 == 1)].reset_index(drop=True)   # with-trend only
    if s.empty: return None
    mt5.initialize(timeout=15000)
    r = mt5.copy_rates_range(MT5[symbol], mt5.TIMEFRAME_M5,
                             s.ts.min().to_pydatetime() - timedelta(days=1),
                             s.ts.max().to_pydatetime() + timedelta(days=2))
    mt5.shutdown()
    df = pd.DataFrame(r); df["dt"] = pd.to_datetime(df["time"], unit="s")
    hi, lo = df.high.values, df.low.values
    posmap = {t: i for i, t in enumerate(df.dt.values)}
    out = []
    for k in range(len(s)):
        i = posmap.get(s.ts.values[k])
        if i is None: continue
        e, rk, buy = s.entry.iloc[k], s.risk.iloc[k], (s.bias.iloc[k] == "buy")
        if rk <= 0: continue
        j = slice(i + 1, min(i + 1 + HORIZON, len(df)))
        h, l = hi[j], lo[j]
        if buy: fav = (h - e) / rk; adv = (e - l) / rk
        else:   fav = (e - l) / rk; adv = (h - e) / rk
        out.append((s.ts.iloc[k], fav, adv))
    return out


print("Exit study | with-trend trades | expectancy(R) per scheme | breakeven +0.00")
pooled = {n: [] for (n, *_) in SCHEMES}
per_sym = {}
for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    P = paths_for(sym)
    if not P:
        print(f"{sym}: no data"); continue
    P.sort(key=lambda x: x[0])
    res = {n: [] for (n, *_) in SCHEMES}
    for _, fav, adv in P:
        for (n, tp, be, ts_, td, pa, ptp) in SCHEMES:
            res[n].append(sim(fav, adv, tp, be, ts_, td, pa, ptp))
    per_sym[sym] = res
    for n in res: pooled[n] += res[n]
    print(f"\n### {sym} ({len(P)} with-trend trades)")
    for (n, *_) in SCHEMES:
        r = np.array(res[n]); print(f"  {n:28} exp={r.mean():+.3f}R")

print("\n" + "=" * 60 + "\nPOOLED (all symbols) + split-half robustness")
base = np.array(pooled["baseline TP2.5 BE@1.0"])
for (n, *_) in SCHEMES:
    r = np.array(pooled[n]); half = len(r) // 2
    h1, h2 = r[:half].mean(), r[half:].mean()
    flag = ""
    if n != "baseline TP2.5 BE@1.0":
        b1, b2 = base[:half].mean(), base[half:].mean()
        beats_both = (h1 > b1 + 0.01) and (h2 > b2 + 0.01)
        flag = "  <== beats baseline on BOTH halves" if beats_both else ""
    print(f"  {n:28} exp={r.mean():+.3f}R  (half1 {h1:+.3f} / half2 {h2:+.3f}){flag}")
