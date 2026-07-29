"""Is the SL too tight? Test SL width = 1.5x / 2.0x / 2.5x / 3.0x ATR on
with-trend trades (TP fixed at 2.5R for each width, so R-structure is constant).
A wider SL is harder to hit (fewer premature stop-outs) but so is the TP.
Reports win rate + expectancy(R) per width. If wider SL wins, the user's
'stops too tight' hypothesis is confirmed.
"""
import sqlite3, sys, warnings
from datetime import datetime, timedelta
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
MT5 = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
WIDTHS = [1.5, 2.0, 2.5, 3.0]   # ATR multiples for the SL
RR = 2.5                        # TP = RR * SL width (constant R-structure)
HORIZON = 864

import MetaTrader5 as mt5


def expect(outs):
    n = len(outs)
    if n == 0:
        return 0, float("nan"), float("nan")
    wr = sum(outs) / n
    return n, wr, wr * RR - (1 - wr)


def paths_atr(symbol):
    """Forward fav/adv per trade in ATR units (with-trend trades only)."""
    conn = sqlite3.connect(DB)
    s = pd.read_sql_query("SELECT ts,entry,bias,macro4h,should_scalp,atr FROM samples "
                          "WHERE symbol=? AND label IS NOT NULL", conn, params=(symbol,))
    conn.close()
    s["ts"] = pd.to_datetime(s["ts"])
    a4 = np.zeros(len(s), int)
    a4[((s.bias == "buy") & (s.macro4h == "bullish")).values] = 1
    a4[((s.bias == "sell") & (s.macro4h == "bearish")).values] = 1
    s = s[(s.should_scalp == 1) & (a4 == 1) & (s.atr > 0)].reset_index(drop=True)
    if s.empty:
        return []
    mt5.initialize(timeout=15000)
    r = mt5.copy_rates_range(MT5[symbol], mt5.TIMEFRAME_M5,
                             s.ts.min().to_pydatetime() - timedelta(days=1),
                             s.ts.max().to_pydatetime() + timedelta(days=2))
    mt5.shutdown()
    df = pd.DataFrame(r); df["dt"] = pd.to_datetime(df.time, unit="s")
    hi, lo = df.high.values, df.low.values
    pos = {t: i for i, t in enumerate(df.dt.values)}
    out = []
    for k in range(len(s)):
        i = pos.get(s.ts.values[k])
        if i is None:
            continue
        e, atr, buy = s.entry.iloc[k], s.atr.iloc[k], (s.bias.iloc[k] == "buy")
        j = slice(i + 1, min(i + 1 + HORIZON, len(df)))
        h, l = hi[j], lo[j]
        if buy:
            fav = (h - e) / atr; adv = (e - l) / atr
        else:
            fav = (e - l) / atr; adv = (h - e) / atr
        out.append((fav, adv))
    return out


def sim(fav, adv, width):
    tp = RR * width
    for f, a in zip(fav, adv):
        if a >= width:   # SL hit (conservative: stop first)
            return 0
        if f >= tp:      # TP hit
            return 1
    return None


print(f"SL WIDTH SWEEP | with-trend trades | TP = {RR}R for each width\n")
pooled = {w: [] for w in WIDTHS}
for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    P = paths_atr(sym)
    if not P:
        print(f"{sym}: no data"); continue
    print(f"### {sym} ({len(P)} trades)")
    for w in WIDTHS:
        outs = [o for o in (sim(f, a, w) for f, a in P) if o is not None]
        pooled[w] += outs
        n, wr, exp = expect(outs)
        tag = " (current)" if w == 1.5 else ""
        print(f"  SL {w}xATR: WR={wr*100:>4.1f}%  exp={exp:+.2f}R  n={n}{tag}")
    print()

print("=" * 52 + "\nPOOLED (all symbols)")
best_w, best_e = None, -9
for w in WIDTHS:
    n, wr, exp = expect(pooled[w])
    if exp > best_e:
        best_e, best_w = exp, w
    print(f"  SL {w}xATR: WR={wr*100:>4.1f}%  exp={exp:+.2f}R  n={n}"
          + ("  (current)" if w == 1.5 else ""))
print(f"\n-> BEST: SL {best_w}xATR at {best_e:+.2f}R  (current is 1.5xATR)")
