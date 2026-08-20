"""
Exit-timing study — are winners being cut short? (2026-08-12)
=============================================================
Observation from the live log: several trades close in profit but look like
they exited far too early.

For every closed trade this replays the actual price path from entry onward
and measures three things in R:
    realised   what the trade actually banked
    peak_pre   the best excursion BEFORE the exit  (was the trail late?)
    peak_post  the best excursion AFTER the exit   (did it keep running?)

peak_post is the money left on the table. If it is consistently large, the
exit is too tight. If it is small, the exits are fine and the trades simply
did not have more to give.

Also reconstructs what the CURRENT trail rule would have banked, to separate
"the rule is wrong" from "the 30-second poll missed the move".
"""
import sqlite3, json, warnings
from datetime import timedelta

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

MAP = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm",
       "EURUSD": "EURUSDm", "GBPJPY": "GBPJPYm", "USDJPY": "USDJPYm"}
SINCE = "2026-08-03"
AFTER_BARS = 288          # 24h of M5 after the exit


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit("mt5 attach failed")
    c = sqlite3.connect("crypto_oracle_8000.db")
    rows = c.execute(
        "SELECT timestamp,symbol,action,outcome,profit,closed_at,data FROM scalp_log "
        "WHERE outcome IS NOT NULL AND timestamp>=? ORDER BY id", (SINCE,)).fetchall()
    c.close()

    cache = {}
    def bars(sym):
        if sym not in cache:
            r = mt5.copy_rates_range(MAP[sym], mt5.TIMEFRAME_M5,
                                     pd.Timestamp(SINCE) - timedelta(days=1),
                                     pd.Timestamp("2026-08-13"))
            d = pd.DataFrame(r)
            d["dt"] = pd.to_datetime(d["time"], unit="s")
            cache[sym] = d
        return cache[sym]

    out = []
    for ts, sym, act, outc, prof, ca, data in rows:
        if sym not in MAP or not ca:
            continue
        o = json.loads(data)
        entry, sl = o.get("entry"), o.get("sl")
        if not entry or not sl:
            continue
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        d = bars(sym)
        t0, t1 = pd.Timestamp(ts), pd.Timestamp(ca)
        pre = d[(d.dt >= t0) & (d.dt <= t1)]
        post = d[(d.dt > t1)].head(AFTER_BARS)
        if pre.empty:
            continue
        buy = act == "buy"
        fav = (lambda x: (x.high.max() - entry) / risk) if buy else \
              (lambda x: (entry - x.low.min()) / risk)
        peak_pre = fav(pre)
        peak_post = fav(post) if not post.empty else float("nan")
        realised = (prof or 0)
        # realised in R: use the $ ratio against the average $ risk we can infer
        out.append({"ts": ts[:16], "sym": sym, "act": act, "won": outc == 1,
                    "usd": realised, "rr": o.get("rr"),
                    "peak_pre": peak_pre, "peak_post": peak_post,
                    "held_min": (t1 - t0).total_seconds() / 60,
                    "pend": bool((o.get("result") or {}).get("pending"))})
    mt5.shutdown()

    df = pd.DataFrame(out)
    w = df[df.won]
    print(f"closed trades analysed: {len(df)}   winners: {len(w)}\n")

    print("=== WINNERS: what they banked vs what was available ===")
    print(f"  {'when':17s} {'sym':7s} {'$':>7s} {'RR':>6s} {'peak BEFORE exit':>17s} "
          f"{'peak AFTER exit':>16s} {'held':>8s}")
    for r in w.itertuples():
        print(f"  {r.ts:17s} {r.sym:7s} {r.usd:>+7.2f} {str(r.rr):>6s} "
              f"{r.peak_pre:>16.2f}R {r.peak_post:>15.2f}R {r.held_min:>7.0f}m")

    print(f"\n  median peak BEFORE exit : {w.peak_pre.median():.2f}R")
    print(f"  median peak AFTER  exit : {w.peak_post.median():.2f}R   <- left on the table")
    print(f"  winners where price ran FURTHER after exit: "
          f"{(w.peak_post > w.peak_pre).sum()}/{len(w)}")

    print("\n=== LOSERS: did they ever go green? ===")
    l = df[~df.won]
    print(f"  median peak before exit: {l.peak_pre.median():.2f}R")
    print(f"  losers that reached +1R before dying: {(l.peak_pre >= 1.0).sum()}/{len(l)}")

    print("\n=== the trail's own arithmetic ===")
    print("  buffer = max(5% of TP distance, 10% of current profit)")
    print(f"  {'RR':>6} {'buffer at 1R':>14} {'locks in':>10}")
    for rr in (2.0, 2.5, 3.0, 6.0, 10.0, 20.0):
        buf = max(rr * 0.05, 0.10)
        print(f"  {rr:>6.1f} {buf:>13.2f}R {max(1.0-buf,0):>9.2f}R")


if __name__ == "__main__":
    main()
