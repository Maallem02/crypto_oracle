"""
Exit-rule study (2026-08-01)
============================
Your live exit management is: breakeven when MFE reaches 1.5R, then a trailing
stop once price is 70% of the way to TP. The offline dataset that every other
study is built on assumes a PURE BRACKET (fixed SL, fixed TP, no management) —
so none of those numbers describe what your bot actually does.

This replays the same signals bar-by-bar under each exit policy so the exit
rule itself can be compared on equal footing.

Conservative assumptions (same as the dataset labeller):
  - if a bar touches both SL and TP, SL is assumed first
  - stops are moved using the bar's own extreme, then checked from the NEXT bar,
    so a stop can never be moved and hit inside the same bar with hindsight
"""
import sqlite3, warnings
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

DB      = "offline_dataset.sqlite"
SYMS    = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
RR      = 2.5
HORIZON = 864
FROM, TO = pd.Timestamp("2026-01-09"), pd.Timestamp("2026-07-24")


def run_exit(H, L, i, is_buy, entry, risk, be_trigger, trail_at):
    """Return the trade's R outcome under one exit policy, or None if unresolved."""
    sl = entry - risk if is_buy else entry + risk
    tp = entry + risk * RR if is_buy else entry - risk * RR
    total = abs(tp - entry)
    be_done = False
    end = min(i + 1 + HORIZON, len(H))

    for j in range(i + 1, end):
        # 1. exits are checked against the stop as it stood BEFORE this bar
        if is_buy:
            if L[j] <= sl:
                return (sl - entry) / risk
            if H[j] >= tp:
                return RR
        else:
            if H[j] >= sl:
                return (entry - sl) / risk
            if L[j] <= tp:
                return RR

        # 2. then the stop is updated using this bar's favourable extreme
        moved = (H[j] - entry) if is_buy else (entry - L[j])
        if moved <= 0:
            continue
        if trail_at is not None and moved / total >= trail_at:
            buf = max(total * 0.05, moved * 0.10)
            cand = (H[j] - buf) if is_buy else (L[j] + buf)
            if (is_buy and cand > sl) or ((not is_buy) and cand < sl):
                sl = cand
        elif be_trigger is not None and not be_done and moved >= risk * be_trigger:
            sl = entry
            be_done = True
    return None


POLICIES = [
    ("pure bracket (dataset baseline)", None, None),
    ("BE @1.0R",                        1.0,  None),
    ("BE @1.5R",                        1.5,  None),
    ("BE @2.0R",                        2.0,  None),
    ("BE @1.5R + trail@70%  <- LIVE",   1.5,  0.70),
    ("BE @2.0R + trail@70%",            2.0,  0.70),
    ("trail@70% only (no BE)",          None, 0.70),
    ("trail@50% only (no BE)",          None, 0.50),
]


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit(f"mt5 attach failed: {mt5.last_error()}")

    gates = pd.read_csv("gate_study_rows.csv")
    gates["ts"] = pd.to_datetime(gates.ts)
    gates = gates[gates.b_ok]

    conn = sqlite3.connect(DB)
    res = {name: [] for name, _, _ in POLICIES}
    n_sig = 0

    for sym, msym in SYMS.items():
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,entry,risk FROM samples WHERE symbol=? "
            "AND should_scalp=1 AND r_outcome IS NOT NULL ORDER BY ts",
            conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])
        s = s.merge(gates[["ts", "symbol", "bias"]], on=["ts", "symbol", "bias"], how="inner")

        m5 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M5, FROM, TO))
        m5["dt"] = pd.to_datetime(m5["time"], unit="s")
        pos = {t: k for k, t in enumerate(m5.dt.values)}
        H, L = m5.high.values, m5.low.values

        for ts, bias, entry, risk in zip(s.ts.values, s.bias.values,
                                         s.entry.values, s.risk.values):
            i = pos.get(ts)
            if i is None or risk <= 0:
                continue
            n_sig += 1
            is_buy = bias == "buy"
            for name, be, tr in POLICIES:
                r = run_exit(H, L, i, is_buy, float(entry), float(risk), be, tr)
                if r is not None:
                    res[name].append(r)
    conn.close(); mt5.shutdown()

    print(f"signals replayed: {n_sig:,}\n")
    print(f"  {'exit policy':34s} {'n':>7} {'win%':>6} {'BE%':>6} {'avg R':>8} {'total R':>9}")
    print("  " + "-" * 74)
    base = None
    for name, _, _ in POLICIES:
        a = np.array(res[name])
        if len(a) == 0:
            continue
        wins = (a > 0.05).mean() * 100
        scratch = (np.abs(a) <= 0.05).mean() * 100
        if base is None:
            base = a.mean()
        print(f"  {name:34s} {len(a):>7,} {wins:>5.1f}% {scratch:>5.1f}% "
              f"{a.mean():>+8.4f} {a.sum():>+9.0f}")

    print("\n=== cost of the breakeven rule ===")
    pure = np.array(res["pure bracket (dataset baseline)"])
    live = np.array(res["BE @1.5R + trail@70%  <- LIVE"])
    notr = np.array(res["trail@70% only (no BE)"])
    print(f"  live policy vs pure bracket : {live.mean()-pure.mean():+.4f} R/trade "
          f"({live.sum()-pure.sum():+.0f} R total)")
    print(f"  removing ONLY the BE step   : {notr.mean()-live.mean():+.4f} R/trade "
          f"({notr.sum()-live.sum():+.0f} R total)")
    print(f"  trades scratched to ~0 by live policy: {(np.abs(live)<=0.05).sum():,} "
          f"({(np.abs(live)<=0.05).mean()*100:.1f}%)")


if __name__ == "__main__":
    main()
