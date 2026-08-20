"""
HTF-conflict check — compares win rate for htf_consensus="conflict" trades
vs everything else, focused on the recent decline window (July 6 onward),
to see if conflict trades are what's dragging the recent win rate down.

Run from the project folder:
    python htf_conflict_check.py
"""
import sqlite3
from datetime import datetime

REAL_MAIN_DB = "crypto_oracle_8000.db"
DECLINE_START = "2026-07-06"  # start of the crashed weeks (W28)


def main():
    conn = sqlite3.connect(REAL_MAIN_DB)
    rows = conn.execute("""
        SELECT timestamp, symbol, htf_consensus, outcome, profit
        FROM trade_signals
        WHERE outcome IS NOT NULL
    """).fetchall()
    conn.close()

    trades = [
        {"timestamp": r[0], "symbol": r[1], "htf_consensus": r[2], "outcome": r[3], "profit": r[4]}
        for r in rows
    ]

    recent = [t for t in trades if t["timestamp"] and t["timestamp"] >= DECLINE_START]
    print(f"Trades since {DECLINE_START}: {len(recent)}\n")

    print("=== All-time: win rate by htf_consensus ===")
    for val in ("bullish", "bearish", "conflict"):
        sub = [t for t in trades if t["htf_consensus"] == val]
        if not sub:
            continue
        w = sum(1 for t in sub if t["outcome"] == 1)
        print(f"  {val:10} n={len(sub):5}  win_rate={w/len(sub)*100:5.1f}%")

    print(f"\n=== Since {DECLINE_START} only: win rate by htf_consensus ===")
    for val in ("bullish", "bearish", "conflict"):
        sub = [t for t in recent if t["htf_consensus"] == val]
        if not sub:
            print(f"  {val:10} n=0")
            continue
        w = sum(1 for t in sub if t["outcome"] == 1)
        print(f"  {val:10} n={len(sub):5}  win_rate={w/len(sub)*100:5.1f}%")

    print(f"\n=== Since {DECLINE_START}: what win rate WOULD have been, excluding conflict trades ===")
    non_conflict_recent = [t for t in recent if t["htf_consensus"] != "conflict"]
    if non_conflict_recent:
        w = sum(1 for t in non_conflict_recent if t["outcome"] == 1)
        print(f"  n={len(non_conflict_recent)}  win_rate={w/len(non_conflict_recent)*100:.1f}%  "
              f"(actual with conflict included: {sum(1 for t in recent if t['outcome']==1)/len(recent)*100:.1f}%)")
    else:
        print("  no non-conflict trades in this window")


if __name__ == "__main__":
    main()
