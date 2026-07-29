"""Would using 1H trend alignment instead of 4H be better? The 4H lags
reversals (bot buys downtrends); 1H is faster but noisier. Test both on the
full history: expectancy of signals aligned with 1H vs 4H, and crucially,
are the extra trades 1H admits (that 4H blocks) profitable or just noise?
"""
import sqlite3, sys, warnings
from datetime import datetime, timedelta
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
MT5 = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
RR = 2.5


def trend_series(m5close, tf):
    px = m5close.resample(tf).last().dropna()
    ema = px.ewm(span=20).mean()
    rising = ema > ema.shift(2)
    t = pd.Series("neutral", index=px.index)
    t[(px > ema) & rising] = "bullish"
    t[(px < ema) & ~rising] = "bearish"
    return t


def align(bias, trend):
    if (bias == "buy" and trend == "bullish") or (bias == "sell" and trend == "bearish"):
        return 1
    if (bias == "buy" and trend == "bearish") or (bias == "sell" and trend == "bullish"):
        return -1
    return 0


def expect(lbl):
    if len(lbl) == 0: return float("nan")
    wr = float(np.mean(lbl)); return wr, wr * RR - (1 - wr)


import MetaTrader5 as mt5
mt5.initialize(timeout=15000)
frames = []
conn = sqlite3.connect(DB)
for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    s = pd.read_sql_query("SELECT ts,bias,label,should_scalp FROM samples "
                          "WHERE symbol=? AND label IS NOT NULL AND should_scalp=1",
                          conn, params=(sym,))
    if s.empty: continue
    s["ts"] = pd.to_datetime(s["ts"])
    r = mt5.copy_rates_range(MT5[sym], mt5.TIMEFRAME_M5,
                             s.ts.min().to_pydatetime() - timedelta(days=2), s.ts.max().to_pydatetime())
    df = pd.DataFrame(r); df.index = pd.to_datetime(df.time, unit="s")
    t1 = trend_series(df.close, "1h").reindex(df.index, method="ffill")
    t4 = trend_series(df.close, "4h").reindex(df.index, method="ffill")
    posmap1 = t1.to_dict(); posmap4 = t4.to_dict()
    # nearest prior bar trend for each signal
    idx = df.index.values
    s["tr1"] = [t1.iloc[t1.index.searchsorted(ts, "right")-1] if len(t1) else "neutral" for ts in s.ts]
    s["tr4"] = [t4.iloc[t4.index.searchsorted(ts, "right")-1] if len(t4) else "neutral" for ts in s.ts]
    s["symbol"] = sym
    frames.append(s)
conn.close(); mt5.shutdown()
S = pd.concat(frames, ignore_index=True)
S["a1"] = [align(b, t) for b, t in zip(S.bias, S.tr1)]
S["a4"] = [align(b, t) for b, t in zip(S.bias, S.tr4)]
print(f"tradeable signals: {len(S)}")

def line(name, mask):
    lbl = S.label[mask].values
    if len(lbl) == 0: print(f"  {name:34} n=0"); return
    wr, ex = expect(lbl)
    print(f"  {name:34} n={len(lbl):>5} WR={wr*100:>4.1f}% exp={ex:+.2f}R")

print("\n=== CURRENT (align with 4H) vs PROPOSED (align with 1H) ===")
line("baseline: ALL signals", S.index >= 0)
line("WITH 4H (current rule)", S.a4 == 1)
line("WITH 1H (proposed rule)", S.a1 == 1)
line("WITH BOTH 1H+4H (strictest)", (S.a1 == 1) & (S.a4 == 1))

print("\n=== the decisive question: trades where 1H and 4H DISAGREE ===")
line("1H says GO, 4H says NO (1H-only adds)", (S.a1 == 1) & (S.a4 != 1))
line("4H says GO, 1H says NO (4H-only, 1H would skip)", (S.a4 == 1) & (S.a1 != 1))

print("\n=== per symbol: WITH-4H vs WITH-1H expectancy ===")
for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    sub = S[S.symbol == sym]
    _, e4 = expect(sub.label[sub.a4 == 1].values)
    _, e1 = expect(sub.label[sub.a1 == 1].values)
    n4 = (sub.a4 == 1).sum(); n1 = (sub.a1 == 1).sum()
    print(f"  {sym:7} 4H: {e4:+.2f}R (n={n4})   1H: {e1:+.2f}R (n={n1})   "
          f"{'1H better' if e1 > e4 + 0.02 else '4H better' if e4 > e1 + 0.02 else 'tie'}")
