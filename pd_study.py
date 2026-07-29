"""Does premium/discount POSITION predict outcome? Test the user's idea that
P/D deserves more weight. On with-trend trades (the ones that matter), bucket
BUYS and SELLS by P/D position and measure win-rate + expectancy.

If deeper discount -> higher buy WR (and deeper premium -> higher sell WR),
P/D carries real signal and we tighten the requirement. If flat, it doesn't.
"""
import sqlite3, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
RR = 2.5


def expect(lbl):
    if len(lbl) == 0: return float("nan")
    wr = float(np.mean(lbl)); return wr, wr * RR - (1 - wr)


conn = sqlite3.connect(DB)
d = pd.read_sql_query(
    "SELECT symbol,bias,pd_pos,label,should_scalp,macro4h FROM samples WHERE label IS NOT NULL", conn)
conn.close()
# with-trend only
a = np.zeros(len(d), int)
a[((d.bias=="buy")&(d.macro4h=="bullish")).values] = 1
a[((d.bias=="sell")&(d.macro4h=="bearish")).values] = 1
d = d[(d.should_scalp==1) & (a==1) & d.pd_pos.notna()].copy()
d = d[(d.pd_pos>=0) & (d.pd_pos<=100)]
print(f"with-trend signals with valid P/D: {len(d)}  (pd_pos {d.pd_pos.min():.0f}-{d.pd_pos.max():.0f}, med {d.pd_pos.median():.0f})")

bins = [0, 20, 35, 50, 65, 80, 100]
labels = ["0-20 deep disc", "20-35 disc", "35-50 eq-low", "50-65 eq-high", "65-80 prem", "80-100 deep prem"]
d["bucket"] = pd.cut(d.pd_pos, bins=bins, labels=labels, include_lowest=True)

for side in ["buy", "sell"]:
    sub = d[d.bias==side]
    print(f"\n=== {side.upper()}S by P/D position (n={len(sub)}) ===")
    print(f"  {'P/D zone':18}{'n':>6}{'WR':>8}{'exp':>8}")
    for lab in labels:
        g = sub[sub.bucket==lab]
        if len(g)==0: continue
        wr, ex = expect(g.label.values)
        print(f"  {lab:18}{len(g):>6}{wr*100:>7.1f}%{ex:>+8.2f}R")
    # theory: buys want LOW pd_pos (discount), sells want HIGH (premium)
    if side=="buy":
        deep = sub[sub.pd_pos<=35]; shallow = sub[sub.pd_pos>50]
    else:
        deep = sub[sub.pd_pos>=65]; shallow = sub[sub.pd_pos<50]
    _, ed = expect(deep.label.values); _, es = expect(shallow.label.values)
    print(f"  --> favorable zone exp {ed:+.2f}R (n={len(deep)}) vs unfavorable {es:+.2f}R (n={len(shallow)}) "
          f"| gap {ed-es:+.2f}R")
