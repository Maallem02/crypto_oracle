"""
Trade exit review v3 — runs against the REAL live main db (crypto_oracle_8000.db),
which has 1298 trade_signals rows with mfe_r/mae_r already populated (vs the
136-row subset we had from the shared ml db). Same analysis, much bigger sample.

Run from the project folder:
    python trade_exit_review.py
"""
import sqlite3

REAL_MAIN_DB = "crypto_oracle_8000.db"


def main():
    conn = sqlite3.connect(REAL_MAIN_DB)
    rows = conn.execute("""
        SELECT symbol, action, outcome, profit, mfe_r, mae_r, rr_ratio, timeframe, closed_at, timestamp
        FROM trade_signals
        WHERE outcome IS NOT NULL
    """).fetchall()
    conn.close()

    trades = [
        {
            "symbol": r[0], "action": r[1], "outcome": r[2], "profit": r[3],
            "mfe_r": r[4], "mae_r": r[5], "rr_ratio": r[6], "timeframe": r[7],
            "closed_at": r[8], "timestamp": r[9],
        }
        for r in rows
    ]

    total = len(trades)
    with_mfe = [t for t in trades if t["mfe_r"] is not None]
    print(f"Total closed trades: {total}")
    print(f"Closed trades with MFE/MAE tracked: {len(with_mfe)}\n")

    if not with_mfe:
        print("No MFE/MAE data on closed trades yet.")
        return

    n = len(with_mfe)
    wins = [t for t in with_mfe if t["outcome"] == 1]
    losses = [t for t in with_mfe if t["outcome"] != 1]
    print(f"Of tracked trades: {len(wins)} wins, {len(losses)} losses "
          f"({len(wins)/n*100:.1f}% win rate)\n")

    buckets = [(-99, 0), (0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 99)]
    print("=== How far did trades get (max favorable excursion, in R)? ===")
    for lo, hi in buckets:
        in_bucket = [t for t in with_mfe if lo <= t["mfe_r"] < hi]
        if not in_bucket:
            continue
        w = sum(1 for t in in_bucket if t["outcome"] == 1)
        pct = len(in_bucket) / n * 100
        print(f"  MFE {lo:>5.1f} to {hi:<5.1f}R: {len(in_bucket):4} trades ({pct:4.1f}%) "
              f"-> {w}/{len(in_bucket)} ended as wins")

    reached_be = [t for t in with_mfe if t["mfe_r"] >= 1.5]
    reached_trail_zone = [t for t in with_mfe if t["mfe_r"] >= 2.0]
    reached_tp = [t for t in with_mfe if t["rr_ratio"] and t["mfe_r"] >= t["rr_ratio"] * 0.95]

    print(f"\n=== Deep-profit trades that didn't convert ===")
    print(f"Reached >=1.5R favorable (breakeven-trigger zone): {len(reached_be)} "
          f"({len(reached_be)/n*100:.1f}% of tracked trades)")
    if reached_be:
        loss_after = [t for t in reached_be if t["profit"] is not None and t["profit"] < 0]
        scratch = [t for t in reached_be if t["profit"] is not None and -0.5 <= t["profit"] <= 0.5]
        real_win = [t for t in reached_be if t["profit"] is not None and t["profit"] > 0.5]
        print(f"  -> closed as a LOSS despite reaching 1.5R+: {len(loss_after)}  <-- key number")
        print(f"  -> closed near breakeven (scratch): {len(scratch)}")
        print(f"  -> closed as a real win (profit > 0.5): {len(real_win)}")

    print(f"\nReached >=2.0R favorable (~70% of typical TP, trail zone): "
          f"{len(reached_trail_zone)} ({len(reached_trail_zone)/n*100:.1f}%)")
    print(f"Reached ~full TP target (>=95% of rr_ratio): "
          f"{len(reached_tp)} ({len(reached_tp)/n*100:.1f}%)")

    avg_mfe = sum(t["mfe_r"] for t in with_mfe) / n
    mae_vals = [t["mae_r"] for t in with_mfe if t["mae_r"] is not None]
    avg_mae = sum(mae_vals) / len(mae_vals) if mae_vals else 0
    print(f"\nAverage MFE across tracked trades: {avg_mfe:+.2f}R")
    print(f"Average MAE across tracked trades: {avg_mae:+.2f}R")

    print("\n=== By symbol ===")
    symbols = sorted(set(t["symbol"] for t in with_mfe))
    for s in symbols:
        sub = [t for t in with_mfe if t["symbol"] == s]
        w = sum(1 for t in sub if t["outcome"] == 1)
        avg_m = sum(t["mfe_r"] for t in sub) / len(sub)
        print(f"  {s:10} n={len(sub):4}  win_rate={w/len(sub)*100:5.1f}%  avg_mfe={avg_m:+.2f}R")

    print("\n=== By timeframe ===")
    for tf in sorted(set(t["timeframe"] for t in with_mfe)):
        sub = [t for t in with_mfe if t["timeframe"] == tf]
        w = sum(1 for t in sub if t["outcome"] == 1)
        avg_m = sum(t["mfe_r"] for t in sub) / len(sub)
        print(f"  {tf:10} n={len(sub):4}  win_rate={w/len(sub)*100:5.1f}%  avg_mfe={avg_m:+.2f}R")


if __name__ == "__main__":
    main()
