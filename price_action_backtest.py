"""
Price-action strategy backtest (2026-08-01)
===========================================
Replays features/price_action/strategy.py bar-by-bar over real M15 history,
with the H4 chart as the trend filter, and labels every signal with the SAME
exit policy the live bot now runs (BE 1.5R + trail from 40%).

That last part matters: comparing a new strategy against LG-primary is only
meaningful if both are measured under identical exit management and identical
bracket assumptions.

No-lookahead: at bar i the strategy only ever sees df.iloc[:i+1], and the H4
frame is truncated to the last CLOSED H4 bar at that moment.
"""
import warnings
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

import features.price_action.strategy as PA

SYMS    = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
FROM    = pd.Timestamp("2026-01-09")
TO      = pd.Timestamp("2026-07-24")
WARMUP  = 150          # M15 bars of history the strategy needs
HORIZON = 300          # M15 bars to resolve a trade (~3 days)
BE_R    = 1.5
TRAIL   = 0.40
COST_R  = 0.04         # spread+slippage, same assumption used elsewhere


def run_exit(H, L, start, is_buy, entry, risk, tp):
    """Live exit policy: BE at 1.5R, trail once 40% of the way to TP."""
    sl = entry - risk if is_buy else entry + risk
    total = abs(tp - entry)
    be_done = False
    end = min(start + HORIZON, len(H))
    for j in range(start, end):
        if is_buy:
            if L[j] <= sl:
                return (sl - entry) / risk
            if H[j] >= tp:
                return (tp - entry) / risk
        else:
            if H[j] >= sl:
                return (entry - sl) / risk
            if L[j] <= tp:
                return (entry - tp) / risk
        moved = (H[j] - entry) if is_buy else (entry - L[j])
        if moved <= 0:
            continue
        if moved / total >= TRAIL:
            buf = max(total * 0.05, moved * 0.10)
            cand = (H[j] - buf) if is_buy else (L[j] + buf)
            if (is_buy and cand > sl) or ((not is_buy) and cand < sl):
                sl = cand
        elif not be_done and moved >= risk * BE_R:
            sl = entry
            be_done = True
    return None


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit(f"mt5 attach failed: {mt5.last_error()}")

    rows, reasons = [], {}
    for sym, msym in SYMS.items():
        m15 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M15, FROM, TO))
        h4  = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_H4,  FROM, TO))
        if m15.empty or h4.empty:
            print(f"  {sym}: no data"); continue
        m15["dt"] = pd.to_datetime(m15["time"], unit="s")
        h4["dt"]  = pd.to_datetime(h4["time"],  unit="s")
        H, L = m15.high.values, m15.low.values
        h4_dt = h4.dt.values
        cols = ["open", "high", "low", "close"]
        m15_ohlc = m15[cols]
        h4_ohlc  = h4[cols]

        last_exit = -1
        for i in range(WARMUP, len(m15) - 2):
            if i <= last_exit:                     # one position at a time
                continue
            j = int(np.searchsorted(h4_dt, m15.dt.values[i], side="right")) - 1
            if j < 30:
                continue
            sig = PA.price_action_signal(m15_ohlc.iloc[i - WARMUP:i + 1],
                                         h4_ohlc.iloc[max(0, j - 60):j + 1])
            if not sig.get("detected"):
                reasons[sig.get("reason", "?")] = reasons.get(sig.get("reason", "?"), 0) + 1
                continue

            is_buy = sig["action"] == "buy"
            entry  = sig["entry"]
            risk   = abs(entry - sig["sl"])
            if risk <= 0:
                continue
            r = run_exit(H, L, i + 1, is_buy, entry, risk, sig["tp1"])
            if r is None:
                continue
            rows.append({"symbol": sym, "ts": m15.dt.values[i], "action": sig["action"],
                         "setup": sig["setup"], "pattern": sig["pattern"],
                         "trend": sig["trend"], "touches": sig["level_touches"],
                         "rr": sig["rr_ratio"], "r": r})
            last_exit = i + 5                      # crude cooldown, avoids stacking
    mt5.shutdown()

    d = pd.DataFrame(rows)
    print(f"\n=== why signals were skipped (top reasons) ===")
    for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:8]:
        print(f"  {k:28s} {v:>9,}")

    if d.empty:
        print("\nNO SIGNALS AT ALL — strategy never fired."); return

    def stat(name, g):
        if len(g) < 5:
            print(f"  {name:30s} n={len(g)}"); return
        se = g.r.std(ddof=1) / np.sqrt(len(g))
        print(f"  {name:30s} n={len(g):>5,}  WR={(g.r>0.05).mean()*100:>5.1f}%  "
              f"avg_R={g.r.mean():>+7.4f} +-{1.96*se:.4f}  net={g.r.mean()-COST_R:>+7.4f}  "
              f"total={g.r.sum():>+7.1f}")

    print(f"\n=== PRICE ACTION overall (exit policy = live: BE1.5R + trail40%) ===")
    stat("ALL", d)
    print(f"\n=== by setup ===")
    for k, g in d.groupby("setup"): stat(k, g)
    print(f"\n=== by symbol ===")
    for k, g in d.groupby("symbol"): stat(k, g)
    print(f"\n=== by confirmation pattern ===")
    for k, g in d.groupby("pattern"): stat(k, g)
    print(f"\n=== by level strength (touches) ===")
    for k, g in d.groupby("touches"): stat(f"{k} touches", g)

    print(f"\n=== BENCHMARK ===")
    print(f"  LG-primary under the same exit policy : avg_R +0.2182  (n=21,703)")
    print(f"  price action                          : avg_R {d.r.mean():+.4f}  (n={len(d):,})")
    print(f"  trades per month (all 4 symbols)      : {len(d)/6.5:.1f}")


if __name__ == "__main__":
    main()
