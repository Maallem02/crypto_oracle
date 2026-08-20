"""
Pending-order expiry study (2026-08-08)
=======================================
65% of pending orders (37 of 57 this week) were cancelled unfilled at the
120-minute cutoff. Was that cutoff costing money or saving it?

For each cancelled order we know the exact limit price, SL and TP that were
sitting on the broker. This replays M5 history forward from the moment it was
placed and asks: would price have reached the limit later, and if so what
would the trade have done under the live exit policy (BE 1.5R + trail 40%)?

Also re-checks the FILLED ones — how long did they take to fill? If most fill
inside 30 minutes, a longer expiry only adds late, low-quality fills.
"""
import sqlite3, json, warnings, importlib.util
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")
_s = importlib.util.spec_from_file_location("es", "exit_rule_study.py")
es = importlib.util.module_from_spec(_s); _s.loader.exec_module(es)

MAP = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm",
       "EURUSD": "EURUSDm", "GBPJPY": "GBPJPYm", "USDJPY": "USDJPYm"}
WINDOWS = (30, 60, 120, 240, 480, 1440)      # minutes to wait for a fill


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit("mt5 attach failed")
    c = sqlite3.connect("crypto_oracle_8000.db")
    rows = c.execute("SELECT timestamp,symbol,action,outcome,profit,data FROM scalp_log "
                     "WHERE timestamp>='2026-07-30' ORDER BY id").fetchall()
    c.close()

    cache = {}
    def bars(sym):
        if sym not in cache:
            r = mt5.copy_rates_range(MAP[sym], mt5.TIMEFRAME_M5,
                                     pd.Timestamp("2026-07-29"), pd.Timestamp("2026-08-09"))
            d = pd.DataFrame(r)
            d["dt"] = pd.to_datetime(d["time"], unit="s")
            cache[sym] = d
        return cache[sym]

    filled_lag, cancelled = [], []
    for ts, sym, act, out, prof, data in rows:
        o = json.loads(data); res = o.get("result") or {}
        if not res.get("pending") or sym not in MAP:
            continue
        rec = {"ts": pd.Timestamp(ts), "sym": sym, "act": act,
               "entry": o.get("entry"), "sl": o.get("sl"), "tp": o.get("tp1"),
               "outcome": out, "profit": prof}
        (filled_lag if out is not None else cancelled).append(rec)

    # ── how long did the FILLED ones take? ────────────────────────────────
    print("=== time-to-fill of orders that DID fill ===")
    lags = []
    for r in filled_lag:
        d = bars(r["sym"]); sub = d[d.dt >= r["ts"]]
        if sub.empty:
            continue
        buy = r["act"] == "buy"
        hit = sub[(sub.low <= r["entry"]) if buy else (sub.high >= r["entry"])]
        if not hit.empty:
            lags.append((hit.dt.iloc[0] - r["ts"]).total_seconds() / 60)
    if lags:
        lags = np.array(lags)
        for q in (50, 75, 90, 95):
            print(f"  p{q}: {np.percentile(lags, q):>7.1f} min")
        print(f"  filled within 30min: {(lags<=30).mean()*100:.0f}%   "
              f"60min: {(lags<=60).mean()*100:.0f}%   120min: {(lags<=120).mean()*100:.0f}%")

    # ── what would the CANCELLED ones have done? ──────────────────────────
    print(f"\n=== the {len(cancelled)} cancelled orders, replayed with longer expiry ===")
    print(f"  {'expiry':>8} {'would fill':>11} {'resolved':>9} {'wins':>6} {'avg R':>8} {'total R':>9}")
    for w in WINDOWS:
        fills = rs = wins = 0
        tot = 0.0
        for r in cancelled:
            d = bars(r["sym"]); sub = d[(d.dt >= r["ts"]) & (d.dt <= r["ts"] + pd.Timedelta(minutes=w))]
            if sub.empty:
                continue
            buy = r["act"] == "buy"
            hit = sub[(sub.low <= r["entry"]) if buy else (sub.high >= r["entry"])]
            if hit.empty:
                continue
            fills += 1
            i = d.index[d.dt == hit.dt.iloc[0]][0]
            risk = abs(r["entry"] - r["sl"])
            if risk <= 0:
                continue
            R = es.run_exit(d.high.values, d.low.values, i, buy,
                            float(r["entry"]), float(risk), 1.5, 0.40)
            if R is None:
                continue
            rs += 1; wins += R > 0.05; tot += R
        avg = tot / rs if rs else float("nan")
        star = "  <- current setting" if w == 120 else ""
        print(f"  {w:>6}min {fills:>10} {rs:>9} {wins:>6} {avg:>+8.3f} {tot:>+9.1f}{star}")

    # ── split by direction, since pending sells are 0/9 live ──────────────
    print("\n=== cancelled orders by direction (1440min window) ===")
    for act in ("buy", "sell"):
        rs = wins = 0; tot = 0.0
        for r in [x for x in cancelled if x["act"] == act]:
            d = bars(r["sym"]); sub = d[(d.dt >= r["ts"]) & (d.dt <= r["ts"] + pd.Timedelta(minutes=1440))]
            if sub.empty: continue
            buy = act == "buy"
            hit = sub[(sub.low <= r["entry"]) if buy else (sub.high >= r["entry"])]
            if hit.empty: continue
            i = d.index[d.dt == hit.dt.iloc[0]][0]
            risk = abs(r["entry"] - r["sl"])
            if risk <= 0: continue
            R = es.run_exit(d.high.values, d.low.values, i, buy, float(r["entry"]), float(risk), 1.5, 0.40)
            if R is None: continue
            rs += 1; wins += R > 0.05; tot += R
        if rs:
            print(f"  {act:5s} n={rs:>3}  wins={wins:>3} ({wins/rs*100:>5.1f}%)  "
                  f"avg_R={tot/rs:>+7.3f}  total={tot:>+7.1f}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
