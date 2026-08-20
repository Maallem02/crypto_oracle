"""
Targeted rejection-reason trend — checks whether specific, meaningful
rejection reasons (as opposed to the structural "always disabled" ones)
spiked around the July 6 performance cliff.

Run from the project folder:
    python rejection_trend.py
"""
import sqlite3
from datetime import datetime

REAL_MAIN_DB = "crypto_oracle_8000.db"

TARGET_PATTERNS = [
    "regime_choppy_m1_skipped",
    "htf_conflict_confirmed",
    "htf_contra",
    "no_htf_bias_agreement",
    "no_pullback_yet",
    "not_near_edge",
]


def week_bucket(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str)
    except Exception:
        return "unknown"
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def main():
    conn = sqlite3.connect(REAL_MAIN_DB)
    cur = conn.cursor()

    total_by_week = {}
    for row in cur.execute("SELECT timestamp FROM rejection_log"):
        w = week_bucket(row[0])
        total_by_week[w] = total_by_week.get(w, 0) + 1

    pattern_by_week = {p: {} for p in TARGET_PATTERNS}
    for pattern in TARGET_PATTERNS:
        like = f"%{pattern}%"
        for row in cur.execute(
            "SELECT timestamp FROM rejection_log WHERE reason LIKE ?", (like,)
        ):
            w = week_bucket(row[0])
            pattern_by_week[pattern][w] = pattern_by_week[pattern].get(w, 0) + 1

    conn.close()

    weeks = sorted(total_by_week.keys())
    print(f"{'week':10} {'total_rejections':>16}  " + "  ".join(f"{p[:18]:>18}" for p in TARGET_PATTERNS))
    for w in weeks:
        total = total_by_week[w]
        counts = [pattern_by_week[p].get(w, 0) for p in TARGET_PATTERNS]
        pct_strs = [f"{c:>10} ({c/total*100:4.1f}%)" for c in counts]
        print(f"{w:10} {total:16}  " + "  ".join(pct_strs))


if __name__ == "__main__":
    main()
