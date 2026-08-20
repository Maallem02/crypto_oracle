"""
Trail-trigger study: % of TP  vs  fixed R (2026-08-10)
======================================================
The live trail arms at 40% of the distance to TP. The exit study that produced
that 40% used a FIXED 2.5R bracket on every trade — and at RR 2.5, "40% of TP"
IS 1.0R. The two rules are numerically identical in that dataset, so it could
not distinguish them. The pending-entry system now produces RR of 6-25, where
40% of TP means 2.5R-10R and the trail effectively never arms.

This rebuilds the comparison on trades that actually have high RR: a pending
limit placed 1xATR better than market with the SL left where structure put it,
which is what the live risk-based entry does (entry moves toward the stop, TP
stays put, so RR inflates).

Policies compared, everything else identical (BE 1.5R backstop, same buffer):
    pct_40 : trail once moved >= 0.40 * (TP - entry)      [current live]
    r_1.0  : trail once moved >= 1.0 * risk               [proposed]
    r_0.75 / r_1.25 / r_1.5 : sensitivity around it
"""
import sqlite3, warnings
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

SYMS = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
FROM, TO = pd.Timestamp("2026-01-09"), pd.Timestamp("2026-07-24")
HORIZON  = 864
BE_R     = 1.5
WAIT     = 24        # M5 bars to wait for the limit to fill (2h)


def run_exit(H, L, start, is_buy, entry, risk, tp, *, trail_pct=None, trail_r=None):
    """One exit policy. Give either trail_pct (fraction of TP) or trail_r (R)."""
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
        armed = (moved / total >= trail_pct) if trail_pct is not None \
                else (moved >= risk * trail_r)
        if armed:
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
        raise SystemExit("mt5 attach failed")
    gates = pd.read_csv("gate_study_rows.csv")
    gates["ts"] = pd.to_datetime(gates.ts); gates = gates[gates.b_ok]
    conn = sqlite3.connect("offline_dataset.sqlite")

    rows = []
    for sym, msym in SYMS.items():
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,entry,risk,atr,macro4h FROM samples WHERE symbol=? "
            "AND should_scalp=1 AND r_outcome IS NOT NULL ORDER BY ts", conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])
        s = s.merge(gates[["ts", "symbol", "bias"]], on=["ts", "symbol", "bias"])
        need = np.where(s.bias == "buy", "bullish", "bearish")
        s = s[(s.macro4h == "neutral") | (s.macro4h == need)]     # 4H filter, as live
        m5 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M5, FROM, TO))
        m5["dt"] = pd.to_datetime(m5["time"], unit="s")
        pos = {t: i for i, t in enumerate(m5.dt.values)}
        H, L = m5.high.values, m5.low.values

        for ts, bias, entry, risk, atr in zip(s.ts.values, s.bias.values,
                                              s.entry.values, s.risk.values, s.atr.values):
            i = pos.get(ts)
            if i is None or i < 260 or risk <= 0 or not atr or atr <= 0:
                continue
            buy = bias == "buy"
            sl_price = entry - risk if buy else entry + risk
            tp_price = entry + risk * 2.5 if buy else entry - risk * 2.5

            # pending limit 1xATR better; SL unchanged -> risk shrinks, RR inflates
            lim = entry - atr if buy else entry + atr
            prisk = abs(lim - sl_price)
            if prisk <= 0:
                continue
            rr = abs(tp_price - lim) / prisk
            fill = None
            for j in range(i + 1, min(i + 1 + WAIT, len(H))):
                if (L[j] <= lim) if buy else (H[j] >= lim):
                    fill = j
                    break
            if fill is None:
                continue
            rec = {"symbol": sym, "rr": rr}
            for name, kw in (("pct_40", dict(trail_pct=0.40)),
                             ("r_0.75", dict(trail_r=0.75)),
                             ("r_1.0",  dict(trail_r=1.0)),
                             ("r_1.25", dict(trail_r=1.25)),
                             ("r_1.5",  dict(trail_r=1.5))):
                rec[name] = run_exit(H, L, fill, buy, lim, prisk, tp_price, **kw)
            rows.append(rec)
    conn.close(); mt5.shutdown()

    d = pd.DataFrame(rows).dropna()
    pol = ["pct_40", "r_0.75", "r_1.0", "r_1.25", "r_1.5"]
    print(f"filled pending trades simulated: {len(d):,}")
    print(f"RR distribution: p25={d.rr.quantile(.25):.1f}  median={d.rr.median():.1f}  "
          f"p75={d.rr.quantile(.75):.1f}  p95={d.rr.quantile(.95):.1f}\n")

    print(f"  {'policy':10} {'avg R':>9} {'total R':>10} {'win%':>7} {'vs current':>11}")
    print("  " + "-" * 52)
    base = d["pct_40"].mean()
    for p in pol:
        star = "  <- current" if p == "pct_40" else ""
        print(f"  {p:10} {d[p].mean():>+9.4f} {d[p].sum():>+10.0f} "
              f"{(d[p] > 0.05).mean() * 100:>6.1f}% {d[p].mean() - base:>+11.4f}{star}")

    print("\n=== where it matters: by RR bucket (this is the whole point) ===")
    print(f"  {'RR band':>14} {'n':>6} {'pct_40':>9} {'r_1.0':>9} {'gain':>9}")
    for lo, hi in [(0, 3), (3, 5), (5, 8), (8, 15), (15, 999)]:
        g = d[(d.rr >= lo) & (d.rr < hi)]
        if len(g) < 30:
            continue
        print(f"  {lo:>5}-{hi:<8} {len(g):>6,} {g['pct_40'].mean():>+9.4f} "
              f"{g['r_1.0'].mean():>+9.4f} {g['r_1.0'].mean() - g['pct_40'].mean():>+9.4f}")

    print("\n=== robustness: train (<2026-05-01) is not available here, so split by symbol ===")
    for sym, g in d.groupby("symbol"):
        print(f"  {sym:8s} n={len(g):>5,}  pct_40={g['pct_40'].mean():>+7.4f}  "
              f"r_1.0={g['r_1.0'].mean():>+7.4f}  gain={g['r_1.0'].mean()-g['pct_40'].mean():>+7.4f}")


if __name__ == "__main__":
    main()
