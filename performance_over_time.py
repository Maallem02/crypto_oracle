"""
Performance-over-time check — buckets ALL closed trades (not just the ones
with mfe_r) by week, to see if win rate/expectancy has actually declined
recently, or if the 19.9% WR seen in the mfe-tracked subset is just a small/
skewed sample.

Run from the project folder:
    python performance_over_time.py
"""
import sqlite3
from datetime import datetime
from core.config import runtime


def week_bucket(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str)
    except Exception:
        return "unknown"
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def main():
    conn = sqlite3.connect(runtime.ml_db_path)
    rows = conn.execute("""
        SELECT closed_at, timestamp, outcome, profit, rr_ratio, mfe_r
        FROM trade_signals
        WHERE outcome IS NOT NULL
    """).fetchall()
    conn.close()

    trades = []
    for closed_at, timestamp, outcome, profit, rr_ratio, mfe_r in rows:
        ts = closed_at or timestamp
        trades.append({
            "week": week_bucket(ts),
            "date": ts,
            "outcome": outcome,
            "profit": profit,
            "mfe_r": mfe_r,
        })

    trades.sort(key=lambda t: t["date"] or "")

    print(f"Total closed trades: {len(trades)}\n")

    weeks = sorted(set(t["week"] for t in trades))
    print(f"{'week':10} {'n':>5} {'wins':>6} {'win_rate':>9} {'avg_profit':>11} {'with_mfe':>9}")
    for w in weeks:
        sub = [t for t in trades if t["week"] == w]
        wins = sum(1 for t in sub if t["outcome"] == 1)
        n = len(sub)
        wr = wins / n * 100 if n else 0
        profits = [t["profit"] for t in sub if t["profit"] is not None]
        avg_p = sum(profits) / len(profits) if profits else 0
        with_mfe = sum(1 for t in sub if t["mfe_r"] is not None)
        print(f"{w:10} {n:5} {wins:6} {wr:8.1f}% {avg_p:11.2f} {with_mfe:9}")

    print("\n=== Last 30 vs previous 30 closed trades (by close order) ===")
    with_dates = [t for t in trades if t["date"]]
    last30 = with_dates[-30:]
    prev30 = with_dates[-60:-30] if len(with_dates) >= 60 else []
    for label, group in [("previous 30", prev30), ("last 30", last30)]:
        if not group:
            continue
        wins = sum(1 for t in group if t["outcome"] == 1)
        print(f"{label}: n={len(group)} win_rate={wins/len(group)*100:.1f}%  "
              f"date_range={group[0]['date']} -> {group[-1]['date']}")


if __name__ == "__main__":
    main()
