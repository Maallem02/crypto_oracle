"""Measure the REAL correlation matrix of the 7 live symbols from 6 months
of 1h returns. Confirms which symbols move together (concentrated risk if
held simultaneously) and whether the crypto/metals/forex grouping is right.
"""
import sys, warnings
from datetime import datetime, timedelta
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SYMS = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm",
        "EURUSD": "EURUSDm", "GBPJPY": "GBPJPYm", "USDJPY": "USDJPYm"}

import MetaTrader5 as mt5
if not mt5.initialize(timeout=15000):
    sys.exit("mt5 init failed")
closes = {}
for name, m in SYMS.items():
    mt5.symbol_select(m, True)
    r = mt5.copy_rates_range(m, mt5.TIMEFRAME_H1,
                             datetime.now() - timedelta(days=180), datetime.now())
    if r is None or len(r) == 0:
        print(f"  no data {name}"); continue
    df = pd.DataFrame(r)
    s = pd.Series(df.close.values, index=pd.to_datetime(df.time, unit="s"))
    closes[name] = s
mt5.shutdown()

px = pd.DataFrame(closes).sort_index()
ret = np.log(px / px.shift(1))
ret = ret.dropna(how="any")            # common timestamps only (handles session gaps)
print(f"aligned 1h bars: {len(ret)}  ({ret.index[0]:%Y-%m-%d} -> {ret.index[-1]:%Y-%m-%d})\n")

corr = ret.corr()
order = ["BTC", "ETH", "XAUUSD", "XAGUSD", "EURUSD", "GBPJPY", "USDJPY"]
corr = corr.loc[order, order]

print("CORRELATION MATRIX (1h returns, 6 months):")
print("        " + "".join(f"{c[:6]:>8}" for c in order))
for r_ in order:
    row = "".join(f"{corr.loc[r_, c]:>8.2f}" for c in order)
    print(f"{r_:7} {row}")

print("\nStrong pairs (|corr| >= 0.5):")
seen = set()
for i, a in enumerate(order):
    for b in order[i+1:]:
        c = corr.loc[a, b]
        if abs(c) >= 0.5:
            print(f"  {a:7} <-> {b:7}  {c:+.2f}  ({'move TOGETHER' if c>0 else 'move OPPOSITE'})")
            seen.add((a, b))
if not seen:
    print("  (none >= 0.5)")

print("\nModerate pairs (0.3 <= |corr| < 0.5):")
for i, a in enumerate(order):
    for b in order[i+1:]:
        c = corr.loc[a, b]
        if 0.3 <= abs(c) < 0.5:
            print(f"  {a:7} <-> {b:7}  {c:+.2f}")

# average intra-class vs cross-class correlation to validate grouping
CLS = {"BTC": "crypto", "ETH": "crypto", "XAUUSD": "metals", "XAGUSD": "metals",
       "EURUSD": "forex", "GBPJPY": "forex", "USDJPY": "forex"}
intra, cross = [], []
for i, a in enumerate(order):
    for b in order[i+1:]:
        (intra if CLS[a] == CLS[b] else cross).append(abs(corr.loc[a, b]))
print(f"\nGrouping check: avg |corr| WITHIN a class = {np.mean(intra):.2f} | "
      f"ACROSS classes = {np.mean(cross):.2f}")
