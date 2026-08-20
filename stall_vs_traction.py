"""
Stall-out vs traction comparison — for closed trades with mfe_r tracked,
splits into:
  group A = never reached 1.5R favorable (stalled, 0% historically became wins)
  group B = reached 1.5R+ favorable (traction, 0% historically became losses)

Then compares every numeric feature available at entry time to look for a
leading indicator that predicts which group a trade will fall into.

Run from the project folder:
    python stall_vs_traction.py
"""
import sqlite3
from core.config import runtime

NUMERIC_FIELDS = [
    "scalping_score", "adx", "atr_ratio", "rsi", "stoch_k", "stoch_d",
    "rr_ratio", "volume_ratio", "spread_pct", "symbol_winrate",
    "consecutive_losses", "hour", "day_of_week",
]
CATEGORICAL_FIELDS = ["structure", "pd_zone", "htf_consensus", "lg_strength", "symbol", "timeframe", "action"]


def main():
    conn = sqlite3.connect(runtime.ml_db_path)
    cols = ["mfe_r", "outcome"] + NUMERIC_FIELDS + CATEGORICAL_FIELDS
    query = f"SELECT {', '.join(cols)} FROM trade_signals WHERE outcome IS NOT NULL AND mfe_r IS NOT NULL"
    rows = conn.execute(query).fetchall()
    conn.close()

    trades = [dict(zip(cols, r)) for r in rows]
    stalled = [t for t in trades if t["mfe_r"] < 1.5]
    traction = [t for t in trades if t["mfe_r"] >= 1.5]

    print(f"Stalled (mfe_r < 1.5R): {len(stalled)}")
    print(f"Traction (mfe_r >= 1.5R): {len(traction)}\n")

    print("=== Numeric feature averages: stalled vs traction ===")
    print(f"{'field':22} {'stalled_avg':>12} {'traction_avg':>13} {'diff':>10}")
    for f in NUMERIC_FIELDS:
        s_vals = [t[f] for t in stalled if t[f] is not None]
        t_vals = [t[f] for t in traction if t[f] is not None]
        if not s_vals or not t_vals:
            continue
        s_avg = sum(s_vals) / len(s_vals)
        t_avg = sum(t_vals) / len(t_vals)
        print(f"{f:22} {s_avg:12.2f} {t_avg:13.2f} {t_avg - s_avg:+10.2f}")

    print("\n=== Categorical feature distributions: stalled vs traction ===")
    for f in CATEGORICAL_FIELDS:
        print(f"\n-- {f} --")
        s_counts = {}
        t_counts = {}
        for t in stalled:
            v = t[f]
            s_counts[v] = s_counts.get(v, 0) + 1
        for t in traction:
            v = t[f]
            t_counts[v] = t_counts.get(v, 0) + 1
        all_vals = set(s_counts) | set(t_counts)
        for v in sorted(all_vals, key=lambda x: (x is None, x)):
            s_n = s_counts.get(v, 0)
            t_n = t_counts.get(v, 0)
            s_pct = s_n / len(stalled) * 100 if stalled else 0
            t_pct = t_n / len(traction) * 100 if traction else 0
            print(f"  {str(v):20} stalled: {s_n:4} ({s_pct:5.1f}%)   traction: {t_n:4} ({t_pct:5.1f}%)")


if __name__ == "__main__":
    main()
