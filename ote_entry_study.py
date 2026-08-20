"""
OTE limit-entry study (2026-08-01)
==================================
Question: would placing a LIMIT order in the OTE zone (fib 0.618-0.786 of the
last swing leg) beat entering at the signal price?

It is not enough that filled OTE trades look good — the trades that never fill
are trades you simply do not take, and those may be the ones that ran. So this
compares TOTAL R over the SAME signal population:

    baseline : enter every signal at market, SL 2.5xATR, TP 2.5R
    OTE      : place a limit in the OTE zone, SL beyond the swing extreme,
               TP at the same 2.5R. Unfilled within the expiry = no trade.

Both legs are labelled with the same bracket walk-forward, so the comparison is
apples to apples. Costs are ignored in both (they'd hit baseline harder, since
it takes more trades).
"""
import sqlite3, warnings
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

DB       = "offline_dataset.sqlite"
SYMS     = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
LOOKBACK = 5
WINDOW   = 250
RR       = 2.5
HORIZON  = 864                      # same 3-day bracket horizon as the dataset
EXPIRIES = (12, 24, 48, 96)         # bars to wait for a fill (1h, 2h, 4h, 8h on M5)
FROM, TO = pd.Timestamp("2026-01-09"), pd.Timestamp("2026-07-24")


def swing_mask(high, low, lookback=LOOKBACK):
    w = lookback * 2 + 1
    sh = high >= pd.Series(high).rolling(w, center=True).max().values
    sl = low  <= pd.Series(low ).rolling(w, center=True).min().values
    return np.nan_to_num(sh, nan=0).astype(bool), np.nan_to_num(sl, nan=0).astype(bool)


def last_swings(sh_idx, sl_idx, high, low, i):
    """Last confirmed swing high/low knowable at bar i (centred -> i-LOOKBACK)."""
    cut, lo = i - LOOKBACK, i - WINDOW
    hs = sh_idx[(sh_idx <= cut) & (sh_idx >= lo)]
    ls = sl_idx[(sl_idx <= cut) & (sl_idx >= lo)]
    if len(hs) == 0 or len(ls) == 0:
        return None, None
    return float(high[hs[-1]]), float(low[ls[-1]])


def bracket(highs, lows, start, bias, entry, risk, horizon=HORIZON):
    """Walk forward from bar `start`; return R outcome (+RR / -1 / None)."""
    sl = entry - risk if bias == "buy" else entry + risk
    tp = entry + risk * RR if bias == "buy" else entry - risk * RR
    end = min(start + horizon, len(highs))
    for j in range(start, end):
        hit_sl = lows[j] <= sl if bias == "buy" else highs[j] >= sl
        hit_tp = highs[j] >= tp if bias == "buy" else lows[j] <= tp
        if hit_sl:                       # conservative on a double touch
            return -1.0
        if hit_tp:
            return RR
    return None


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit(f"mt5 attach failed: {mt5.last_error()}")

    gates = pd.read_csv("gate_study_rows.csv")
    gates["ts"] = pd.to_datetime(gates.ts)
    gates = gates[gates.b_ok]                       # current live regime only

    conn = sqlite3.connect(DB)
    out = []
    for sym, msym in SYMS.items():
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,entry,risk,atr,r_outcome FROM samples "
            "WHERE symbol=? AND should_scalp=1 AND r_outcome IS NOT NULL ORDER BY ts",
            conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])
        s = s.merge(gates[["ts", "symbol", "bias"]], on=["ts", "symbol", "bias"], how="inner")

        m5 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M5, FROM, TO))
        m5["dt"] = pd.to_datetime(m5["time"], unit="s")
        pos = {t: i for i, t in enumerate(m5.dt.values)}
        H, L = m5.high.values, m5.low.values
        shm, slm = swing_mask(H, L)
        sh_i, sl_i = np.flatnonzero(shm), np.flatnonzero(slm)

        for ts, bias, entry, risk, atr, r_base in zip(
                s.ts.values, s.bias.values, s.entry.values,
                s.risk.values, s.atr.values, s.r_outcome.values):
            i = pos.get(ts)
            if i is None or i < WINDOW:
                continue
            hi, lo = last_swings(sh_i, sl_i, H, L, i)
            if hi is None or lo is None or hi <= lo:
                continue
            diff = hi - lo

            # OTE zone + structural SL, mirroring engine.compute_ote()
            if bias == "buy":
                z_hi, z_lo = hi - diff * 0.618, hi - diff * 0.786
                ote_entry  = (z_hi + z_lo) / 2
                ote_sl     = lo * 0.999
                if ote_entry >= float(m5.close.values[i]):
                    continue                       # already below the zone: no pullback to wait for
            else:
                z_lo, z_hi = lo + diff * 0.618, lo + diff * 0.786
                ote_entry  = (z_hi + z_lo) / 2
                ote_sl     = hi * 1.001
                if ote_entry <= float(m5.close.values[i]):
                    continue
            ote_risk = abs(ote_entry - ote_sl)
            if ote_risk <= 0:
                continue

            row = {"symbol": sym, "bias": bias, "r_base": float(r_base),
                   "ote_risk_atr": ote_risk / atr if atr else np.nan,
                   "base_risk_atr": risk / atr if atr else np.nan}

            # does price reach the limit within each expiry?
            fill_at = None
            for j in range(i + 1, min(i + max(EXPIRIES) + 1, len(H))):
                touched = (L[j] <= ote_entry) if bias == "buy" else (H[j] >= ote_entry)
                if touched:
                    fill_at = j
                    break
            for e in EXPIRIES:
                ok = fill_at is not None and (fill_at - i) <= e
                row[f"fill_{e}"] = ok
                row[f"r_ote_{e}"] = (bracket(H, L, fill_at, bias, ote_entry, ote_risk)
                                     if ok else None)
            out.append(row)
    conn.close(); mt5.shutdown()

    d = pd.DataFrame(out)
    print(f"signals evaluated (Gate B regime, OTE zone still ahead of price): {len(d):,}\n")

    print("=== RISK per trade: OTE stop is structural, so it is WIDER ===")
    print(f"  baseline SL : {d.base_risk_atr.median():.2f} x ATR (median)")
    print(f"  OTE SL      : {d.ote_risk_atr.median():.2f} x ATR (median)")
    print(f"  -> an OTE trade risks {d.ote_risk_atr.median()/d.base_risk_atr.median():.1f}x more per lot\n")

    base_total = d.r_base.sum()
    print(f"{'expiry':>8} {'fill rate':>10} {'n filled':>9} {'avg R':>8} {'total R':>9}"
          f" {'missed n':>9} {'missed avg R':>13}")
    print("-" * 78)
    for e in EXPIRIES:
        f = d[d[f"fill_{e}"] == True]
        r = f[f"r_ote_{e}"].dropna()
        miss = d[d[f"fill_{e}"] != True]
        print(f"{e:>6} bars {len(f)/len(d)*100:>9.1f}% {len(r):>9,} {r.mean():>+8.4f} "
              f"{r.sum():>+9.0f} {len(miss):>9,} {miss.r_base.mean():>+13.4f}")
    print("-" * 78)
    print(f"{'BASELINE':>8} {'100.0%':>10} {len(d):>9,} {d.r_base.mean():>+8.4f} {base_total:>+9.0f}")

    print("\n=== the honest comparison: total R over the SAME signal set ===")
    print("  (OTE = filled trades only; the unfilled ones are simply not taken)")
    for e in EXPIRIES:
        r = d[d[f"fill_{e}"] == True][f"r_ote_{e}"].dropna()
        print(f"  expiry {e:>3} bars: OTE {r.sum():>+8.0f} R   vs baseline {base_total:>+8.0f} R"
              f"   -> {'OTE WINS' if r.sum() > base_total else 'baseline wins'}")

    print("\n=== what did we give up by waiting? (unfilled trades, 24-bar expiry) ===")
    miss = d[d["fill_24"] != True]
    print(f"  unfilled: {len(miss):,}  their baseline avg R = {miss.r_base.mean():+.4f}"
          f"  total {miss.r_base.sum():+.0f} R")
    print(f"  filled  : {(d.fill_24==True).sum():,}  their baseline avg R = "
          f"{d[d.fill_24==True].r_base.mean():+.4f}")


if __name__ == "__main__":
    main()
