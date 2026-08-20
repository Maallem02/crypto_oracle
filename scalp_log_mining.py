"""
Scalp_log feature mining — extracts fields from the data JSON blob that
never made it into trade_signals (blended_score, news/zone/ema boosts,
condition text) and checks each against real outcome/profit to find a
discriminator we haven't tested yet.

Run from the project folder:
    python scalp_log_mining.py
"""
import sqlite3
import json

REAL_MAIN_DB = "crypto_oracle_8000.db"

CONDITION_KEYWORDS = [
    "CISD", "Counter-trend waived", "FVG", "OB inside", "OB approaching",
    "Deep discount", "Deep premium", "fresh CHoCH", "blocked",
    "sweep_recovery", "wick_rejection",
]


def main():
    conn = sqlite3.connect(REAL_MAIN_DB)
    rows = conn.execute("""
        SELECT data, outcome, profit FROM scalp_log WHERE outcome IS NOT NULL
    """).fetchall()
    conn.close()

    trades = []
    for data_json, outcome, profit in rows:
        try:
            d = json.loads(data_json)
        except Exception:
            continue
        d["_outcome"] = outcome
        d["_profit"] = profit
        trades.append(d)

    n = len(trades)
    print(f"Closed trades with parseable data blob: {n}\n")
    if not n:
        return

    wins = [t for t in trades if t["_outcome"] == 1]
    print(f"Wins: {len(wins)}  Losses: {n - len(wins)}  Win rate: {len(wins)/n*100:.1f}%\n")

    # --- numeric fields not in trade_signals ---
    print("=== blended_score vs score gap ===")
    gaps_win, gaps_loss = [], []
    for t in trades:
        score = t.get("score")
        blended = t.get("blended_score")
        if score is None or blended is None:
            continue
        gap = blended - score
        (gaps_win if t["_outcome"] == 1 else gaps_loss).append(gap)
    if gaps_win and gaps_loss:
        print(f"  avg gap (wins): {sum(gaps_win)/len(gaps_win):+.2f}  n={len(gaps_win)}")
        print(f"  avg gap (loss): {sum(gaps_loss)/len(gaps_loss):+.2f}  n={len(gaps_loss)}")

    for field in ("news_boost", "zone_boost", "ema_boost"):
        win_vals = [t.get(field, 0) for t in trades if t["_outcome"] == 1]
        loss_vals = [t.get(field, 0) for t in trades if t["_outcome"] != 1]
        print(f"\n=== {field} ===")
        print(f"  wins:   avg={sum(win_vals)/len(win_vals):.2f}  "
              f"nonzero={sum(1 for v in win_vals if v)}/{len(win_vals)}")
        print(f"  losses: avg={sum(loss_vals)/len(loss_vals):.2f}  "
              f"nonzero={sum(1 for v in loss_vals if v)}/{len(loss_vals)}")

    # --- condition keyword presence ---
    print("\n=== Condition keyword presence: win rate with vs without ===")
    for kw in CONDITION_KEYWORDS:
        with_kw = []
        without_kw = []
        for t in trades:
            conditions = t.get("conditions", [])
            text = " | ".join(conditions) if isinstance(conditions, list) else str(conditions)
            if kw.lower() in text.lower():
                with_kw.append(t)
            else:
                without_kw.append(t)
        if len(with_kw) < 5:
            continue
        w_with = sum(1 for t in with_kw if t["_outcome"] == 1)
        w_without = sum(1 for t in without_kw if t["_outcome"] == 1)
        print(f"  {kw:22} with: n={len(with_kw):4} wr={w_with/len(with_kw)*100:5.1f}%   "
              f"without: n={len(without_kw):4} wr={w_without/len(without_kw)*100:5.1f}%")

    # --- htf_trends field (raw per-timeframe trend before consensus) ---
    print("\n=== htf_trends 5m/1h combos ===")
    combo_counts = {}
    for t in trades:
        ht = t.get("htf_trends", {})
        combo = f"{ht.get('5m','?')}/{ht.get('1h','?')}"
        combo_counts.setdefault(combo, []).append(t)
    for combo, sub in sorted(combo_counts.items(), key=lambda x: -len(x[1])):
        w = sum(1 for t in sub if t["_outcome"] == 1)
        print(f"  {combo:20} n={len(sub):4}  win_rate={w/len(sub)*100:5.1f}%")


if __name__ == "__main__":
    main()
