"""RR sweep: does a CLOSER take-profit (June's style) improve expectancy, or
just trade win-size for win-frequency? On with-trend trades, fixed SL -1R,
sweep TP from 1.0R..3.0R. Reports win rate AND expectancy(R) per level.
"""
import sys, warnings
from datetime import timedelta
import numpy as np
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from exit_study import paths_for   # reuses validated with-trend path reconstruction

TPS = [1.0, 1.5, 2.0, 2.5, 3.0]


def sim(fav, adv, tp):
    for f, a in zip(fav, adv):
        if a >= 1.0:      # SL at -1R hit first (conservative)
            return 0
        if f >= tp:       # TP reached
            return 1
    return None           # unresolved within horizon


def stats(outs, tp):
    n = len(outs)
    if n == 0:
        return 0, float("nan"), float("nan")
    wr = sum(outs) / n
    return n, wr, wr * tp - (1 - wr)   # win=+tpR, loss=-1R


print("RR SWEEP | with-trend trades | fixed SL -1R | breakeven WR = 1/(1+RR)")
print("(higher win rate is worthless if expectancy drops — watch the R column)\n")

pooled = {tp: [] for tp in TPS}
half_pooled = {tp: ([], []) for tp in TPS}

for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    P = paths_for(sym)
    if not P:
        print(f"{sym}: no data"); continue
    P.sort(key=lambda x: x[0])
    res = {tp: [] for tp in TPS}
    for k, (_, fav, adv) in enumerate(P):
        for tp in TPS:
            o = sim(fav, adv, tp)
            if o is not None:
                res[tp].append(o)
                pooled[tp].append(o)
                (half_pooled[tp][0] if k < len(P)//2 else half_pooled[tp][1]).append(o)
    print(f"### {sym}")
    for tp in TPS:
        n, wr, exp = stats(res[tp], tp)
        be = 1 / (1 + tp) * 100
        print(f"  TP {tp}R: WR={wr*100:>4.1f}% (breakeven {be:.0f}%)  exp={exp:+.2f}R  n={n}")
    print()

print("=" * 58 + "\nPOOLED (all symbols) + split-half robustness")
best_tp, best_exp = None, -9
for tp in TPS:
    n, wr, exp = stats(pooled[tp], tp)
    _, _, e1 = stats(half_pooled[tp][0], tp)
    _, _, e2 = stats(half_pooled[tp][1], tp)
    tag = ""
    if exp > best_exp:
        best_exp, best_tp = exp, tp
    print(f"  TP {tp}R: WR={wr*100:>4.1f}%  exp={exp:+.2f}R  (half1 {e1:+.2f} / half2 {e2:+.2f})")
print(f"\n-> BEST expectancy: TP {best_tp}R at {best_exp:+.2f}R  "
      f"(current setting is 2.5R)")
