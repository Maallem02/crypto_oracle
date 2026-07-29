"""Test the user's hypothesis: when the breaker blocks BUYS (2 consecutive
with-trend buy losses = market turning down), are SELL signals profitable —
including the against-4H sells the bot normally blocks?

If yes, we should flip the breaker from 'block buys' to 'block buys AND
enable sells'. If no, sitting out is correct.
"""
import sqlite3, sys, warnings
from datetime import timedelta
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
RR = 2.5
BLOCK_H = 6


def expect(lbl):
    if len(lbl) == 0: return float("nan")
    wr = float(np.mean(lbl)); return wr * RR - (1 - wr)


# pool all symbols, deduped per (symbol,bias), sorted by time
rows = []
conn = sqlite3.connect(DB)
for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    d = pd.read_sql_query("SELECT ts,bias,label,should_scalp,macro4h FROM samples "
                          "WHERE symbol=? AND label IS NOT NULL", conn, params=(sym,))
    if d.empty: continue
    d["ts"] = pd.to_datetime(d["ts"]); d["symbol"] = sym
    a = np.zeros(len(d), int)
    a[((d.bias=="buy")&(d.macro4h=="bullish")).values] = 1
    a[((d.bias=="sell")&(d.macro4h=="bearish")).values] = 1
    a[((d.bias=="buy")&(d.macro4h=="bearish")).values] = -1
    a[((d.bias=="sell")&(d.macro4h=="bullish")).values] = -1
    d["align4h"] = a
    d = d[d.should_scalp == 1]
    # dedup per (symbol,bias) 45min
    d = d.sort_values("ts"); keep, last = [], {}
    for i, ts, b in zip(d.index, d.ts, d.bias):
        k = (sym, b)
        if k not in last or (ts - last[k]) > timedelta(minutes=45): keep.append(i)
        last[k] = ts
    rows.append(d.loc[keep])
conn.close()
S = pd.concat(rows).sort_values("ts").reset_index(drop=True)
print(f"pooled deduped tradeable signals: {len(S)}")

# walk chronologically, track GLOBAL breaker windows (2 consec with-trend losses per dir)
buy_block_until = sell_block_until = pd.Timestamp.min
run_dir, run = None, 0
S["buys_blocked"] = False; S["sells_blocked"] = False
for idx in S.index:
    ts, bias, lbl, a4 = S.ts[idx], S.bias[idx], S.label[idx], S.align4h[idx]
    S.at[idx, "buys_blocked"] = ts < buy_block_until
    S.at[idx, "sells_blocked"] = ts < sell_block_until
    if a4 == 1:  # with-trend executed-like trade drives the streak
        if lbl == 0:
            if run_dir == bias: run += 1
            else: run_dir, run = bias, 1
            if run >= 2:
                if bias == "buy": buy_block_until = ts + timedelta(hours=BLOCK_H)
                else: sell_block_until = ts + timedelta(hours=BLOCK_H)
        else:
            run_dir, run = None, 0

def rep(name, mask, bias, a4=None):
    m = mask & (S.bias == bias)
    if a4 is not None: m = m & (S.align4h == a4)
    lbl = S.label[m].values
    print(f"  {name:44} n={len(lbl):>4} WR={100*np.mean(lbl) if len(lbl) else 0:4.1f}% exp={expect(lbl):+.2f}R")

print("\n=== THE TEST: sells DURING a buy-block window (market turning down) ===")
rep("baseline: ALL sells", S.bias=="sell", "sell")
rep("baseline: with-4H sells (bot trades these)", (S.align4h==1), "sell")
rep("baseline: against-4H sells (bot BLOCKS these)", (S.align4h==-1), "sell")
print("  --- during a BUY-BLOCK window (buys losing = market down?) ---")
rep("sells while buys blocked (ALL)", S.buys_blocked, "sell")
rep("with-4H sells while buys blocked", S.buys_blocked, "sell", 1)
rep("against-4H sells while buys blocked (the idea)", S.buys_blocked, "sell", -1)

print("\n=== MIRROR: buys during a sell-block window (market turning up) ===")
rep("against-4H buys while sells blocked (the idea)", S.sells_blocked, "buy", -1)
rep("baseline: against-4H buys (bot BLOCKS these)", (S.align4h==-1), "buy")
