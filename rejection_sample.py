"""
Rejection log sampler — pulls total volume and a sample of distinct rejection
reasons so we can design a proper categorized query next, instead of
guessing at patterns blindly against a 1.2M-row table.

Run from the project folder:
    python rejection_sample.py
"""
import sqlite3

REAL_MAIN_DB = "crypto_oracle_8000.db"


def main():
    conn = sqlite3.connect(REAL_MAIN_DB)
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM rejection_log").fetchone()[0]
    print(f"Total rejection_log rows: {total}\n")

    print("=== Date range ===")
    print(cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM rejection_log").fetchone())

    print("\n=== By symbol ===")
    for row in cur.execute("""
        SELECT symbol, COUNT(*) FROM rejection_log GROUP BY symbol ORDER BY 2 DESC
    """):
        print(f"  {row[0]:10} {row[1]}")

    print("\n=== By signal_type ===")
    for row in cur.execute("""
        SELECT signal_type, COUNT(*) FROM rejection_log GROUP BY signal_type ORDER BY 2 DESC LIMIT 20
    """):
        print(f"  {str(row[0]):20} {row[1]}")

    print("\n=== Sample of 40 distinct 'reason' values (most frequent first) ===")
    for row in cur.execute("""
        SELECT reason, COUNT(*) as c FROM rejection_log
        GROUP BY reason ORDER BY c DESC LIMIT 40
    """):
        print(f"  [{row[1]:>7}]  {row[0]}")

    print("\n=== Rows per week (rough volume trend) ===")
    for row in cur.execute("""
        SELECT substr(timestamp, 1, 7) as month, COUNT(*) FROM rejection_log
        GROUP BY month ORDER BY month
    """):
        print(f"  {row[0]}  {row[1]}")

    conn.close()


if __name__ == "__main__":
    main()
