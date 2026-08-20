"""
Regime-shift check — uses ADX and atr_ratio already recorded on every trade
at entry time to see if market character (trend strength / volatility)
actually shifted around the July 6 performance cliff, without needing to
pull fresh price history.

Run from the project folder:
    python regime_check.py
"""
import sqlite3
from datetime import datetime

REAL_MAIN_DB = "crypto_oracle_8000.db"


def week_bucket(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str)
    except Exception:
        return "unknown"
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def main():
    conn = sqlite3.connect(REAL_MAIN_DB)
    rows = conn.execute("""
        SELECT timestamp, symbol, adx, atr_ratio, structure, htf_consensus, outcome
        FROM trade_signals
        WHERE timestamp IS NOT NULL
    """).fetchall()
    conn.close()

    trades = [
        {"week": week_bucket(r[0]), "symbol": r[1], "adx": r[2], "atr_ratio": r[3],
         "structure": r[4], "htf_consensus": r[5], "outcome": r[6]}
        for r in rows
    ]

    weeks = sorted(set(t["week"] for t in trades))
    print(f"{'week':10} {'n':>5} {'avg_adx':>8} {'avg_atr_ratio':>14} {'%conflict_htf':>15} {'win_rate':>9}")
    for w in weeks:
        sub = [t for t in trades if t["week"] == w]
        n = len(sub)
        adx_vals = [t["adx"] for t in sub if t["adx"] is not None]
        atr_vals = [t["atr_ratio"] for t in sub if t["atr_ratio"] is not None]
        avg_adx = sum(adx_vals) / len(adx_vals) if adx_vals else 0
        avg_atr = sum(atr_vals) / len(atr_vals) if atr_vals else 0
        conflict = sum(1 for t in sub if t["htf_consensus"] == "conflict")
        pct_conflict = conflict / n * 100 if n else 0
        closed = [t for t in sub if t["outcome"] is not None]
        wr = (sum(1 for t in closed if t["outcome"] == 1) / len(closed) * 100) if closed else 0
        print(f"{w:10} {n:5} {avg_adx:8.1f} {avg_atr:14.2f} {pct_conflict:14.1f}% {wr:8.1f}%")

    # Focus specifically on BTC and XAUUSD (highest-volume symbols) so
    # the comparison isn't muddied by which symbols happened to trade more
    # in a given week.
    for sym in ("BTC", "XAUUSD", "ETH"):
        print(f"\n=== {sym} only ===")
        for w in weeks:
            sub = [t for t in trades if t["week"] == w and t["symbol"] == sym]
            if not sub:
                continue
            adx_vals = [t["adx"] for t in sub if t["adx"] is not None]
            atr_vals = [t["atr_ratio"] for t in sub if t["atr_ratio"] is not None]
            avg_adx = sum(adx_vals) / len(adx_vals) if adx_vals else 0
            avg_atr = sum(atr_vals) / len(atr_vals) if atr_vals else 0
            print(f"  {w:10} n={len(sub):4}  avg_adx={avg_adx:6.1f}  avg_atr_ratio={avg_atr:5.2f}")


if __name__ == "__main__":
    main()
