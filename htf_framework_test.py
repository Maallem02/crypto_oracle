"""Win rate of the live per-TF HTF framework:
  M5  signal aligned with H1  (already measured elsewhere: 31.1% WR / +0.09R)
  M15 signal aligned with H4
  M30 signal aligned with H4
Generates M15/M30 signals fresh, filters by H4 EMA-trend alignment, labels
with the standard 1.5xATR / 2.5R bracket. Sampled for speed.
"""
import sys, warnings, io
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from features.smc.engine import run_scalping_analysis

MT5SYM = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
TF = {"15m": None, "30m": None}   # filled with mt5 constants below
WINDOW, SL_ATR, RR, HORIZON, STEP = 100, 1.5, 2.5, 576, 3
import MetaTrader5 as mt5
TFMAP = {"15m": mt5.TIMEFRAME_M15, "30m": mt5.TIMEFRAME_M30}


def ema_trend_4h(df):
    px = df["close"].resample("4h").last().dropna()
    ema = px.ewm(span=20).mean()
    t = pd.Series("neutral", index=px.index)
    t[(px > ema) & (ema > ema.shift(2))] = "bullish"
    t[(px < ema) & (ema < ema.shift(2))] = "bearish"
    return t.reindex(df.index, method="ffill").fillna("neutral")


def run(symbol, tf):
    mt5.initialize(timeout=15000)
    r = mt5.copy_rates_range(MT5SYM[symbol], TFMAP[tf],
                             datetime.now() - timedelta(days=186), datetime.now())
    mt5.shutdown()
    df = pd.DataFrame(r); df.index = pd.to_datetime(df.time, unit="s")
    tr = np.maximum(df.high - df.low,
         np.maximum((df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()))
    atr = tr.rolling(14).mean().values
    h4 = ema_trend_4h(df).values
    highs, lows, closes = df.high.values, df.low.values, df.close.values

    def label(i, bias, entry, risk):
        sl = entry - risk if bias == "buy" else entry + risk
        tp = entry + risk * RR if bias == "buy" else entry - risk * RR
        for j in range(i + 1, min(i + 1 + HORIZON, len(df))):
            hs = lows[j] <= sl if bias == "buy" else highs[j] >= sl
            ht = highs[j] >= tp if bias == "buy" else lows[j] <= tp
            if hs: return 0
            if ht: return 1
        return None

    dev = io.StringIO(); n = w = 0
    for i in range(WINDOW, len(df) - 1, STEP):
        try:
            dev.seek(0); dev.truncate(0)
            with redirect_stdout(dev):
                a = run_scalping_analysis(df.iloc[i - WINDOW + 1:i + 1].copy(), symbol, macro_trend=h4[i])
        except Exception:
            continue
        if not a.get("should_scalp"):
            continue
        bias = a.get("bias")
        if bias not in ("buy", "sell"):
            continue
        need = "bullish" if bias == "buy" else "bearish"
        if h4[i] != need:          # THE FRAMEWORK FILTER: align with H4
            continue
        risk = SL_ATR * atr[i]
        if not np.isfinite(risk) or risk <= 0:
            continue
        lbl = label(i, bias, closes[i], risk)
        if lbl is None:
            continue
        n += 1; w += (lbl == 1)
    return n, w


print(f"HTF FRAMEWORK | M15->H4, M30->H4 aligned | bracket 1.5xATR/{RR}R | breakeven 28.6%\n")
agg = {"15m": [0, 0], "30m": [0, 0]}
for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    for tf in ["15m", "30m"]:
        n, w = run(sym, tf)
        wr = w / n if n else 0
        exp = wr * RR - (1 - wr)
        agg[tf][0] += n; agg[tf][1] += w
        print(f"  {sym:7} {tf}->H4: n={n:>4} WR={wr*100:>4.1f}% exp={exp:+.2f}R")
        sys.stdout.flush()
print("\nPOOLED:")
for tf in ["15m", "30m"]:
    n, w = agg[tf]
    wr = w / n if n else 0
    print(f"  {tf}->H4: n={n} WR={wr*100:.1f}% exp={wr*RR-(1-wr):+.2f}R")
print("  (M5->H1 measured separately: 31.1% WR, +0.09R)")
