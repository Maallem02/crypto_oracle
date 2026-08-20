"""
New-symbol check — SOL/BNB/XRP/USDJPY appear heavily in rejection_log but
weren't in the original enabled_symbols default. Checks whether trades are
actually being taken on them, when they started, and how they've performed.

Run from the project folder:
    python new_symbol_check.py
"""
import sqlite3

REAL_MAIN_DB = "crypto_oracle_8000.db"
WATCH_SYMBOLS = ["SOL", "BNB", "XRP", "USDJPY", "BTC", "ETH", "XAUUSD", "EURUSD"]


def main():
    conn = sqlite3.connect(REAL_MAIN_DB)
    cur = conn.cursor()

    print("=== trade_signals: first/last timestamp and count per symbol ===")
    for sym in WATCH_SYMBOLS:
        row = cur.execute("""
            SELECT COUNT(*), MIN(timestamp), MAX(timestamp),
                   SUM(CASE WHEN outcome=1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END)
            FROM trade_signals WHERE symbol=?
        """, (sym,)).fetchone()
        n, first, last, wins, closed = row
        wr = (wins / closed * 100) if closed else 0
        print(f"  {sym:8} total={n:5}  first={first}  last={last}  "
              f"closed={closed}  wins={wins}  win_rate={wr:.1f}%")

    print("\n=== scalp_log: same check (covers all strategies) ===")
    for sym in WATCH_SYMBOLS:
        row = cur.execute("""
            SELECT COUNT(*), MIN(timestamp), MAX(timestamp),
                   SUM(CASE WHEN outcome=1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END)
            FROM scalp_log WHERE symbol=?
        """, (sym,)).fetchone()
        n, first, last, wins, closed = row
        wr = (wins / closed * 100) if closed else 0
        print(f"  {sym:8} total={n:5}  first={first}  last={last}  "
              f"closed={closed}  wins={wins}  win_rate={wr:.1f}%")

    conn.close()


if __name__ == "__main__":
    main()
