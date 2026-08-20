"""
What happened to the signals the meta gate blocked? (2026-08-14)
================================================================
The gate now rejects ~90% of the signals that reach it, so the obvious
question is whether those rejections were right.

Blocked signals never became trades, so there is no recorded outcome. This
replays each one from its rejection timestamp: enter in the signal's
direction at the price then, stop at 2.5xATR, and manage it with the LIVE
exit policy (BE 1.5R, trail from 1.0R). That is an approximation — the real
engine would have placed the stop on structure, not a flat ATR multiple —
but it is applied identically to blocked and passed signals, so the
COMPARISON between the two is fair even if the absolute R is not exact.

Caveat worth stating: the rejection rows do not store entry/sl/tp, only the
score, symbol, direction and time. Adding those to the log would let this be
exact rather than approximate.
"""
import sqlite3, json, warnings
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

MAP = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm",
       "XAGUSD": "XAGUSDm", "EURUSD": "EURUSDm"}
HORIZON = 288          # 24h of M5
BE_R, TRAIL_R = 1.5, 1.0


def run_exit(H, L, i, buy, entry, risk, tp):
    sl = entry - risk if buy else entry + risk
    total = abs(tp - entry); be = False
    for j in range(i, min(i + HORIZON, len(H))):
        if buy:
            if L[j] <= sl: return (sl - entry) / risk
            if H[j] >= tp: return (tp - entry) / risk
        else:
            if H[j] >= sl: return (entry - sl) / risk
            if L[j] <= tp: return (entry - tp) / risk
        moved = (H[j] - entry) if buy else (entry - L[j])
        if moved <= 0: continue
        if moved >= risk * TRAIL_R:
            buf = max(total * 0.05, moved * 0.10)
            cand = (H[j] - buf) if buy else (L[j] + buf)
            if (buy and cand > sl) or ((not buy) and cand < sl): sl = cand
        elif not be and moved >= risk * BE_R:
            sl = entry; be = True
    return None


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit("mt5 attach failed")
    c = sqlite3.connect("crypto_oracle_8000.db")
    rows = c.execute(
        "SELECT timestamp,symbol,data FROM rejection_log "
        "WHERE reason LIKE 'meta_shadow%' ORDER BY id").fetchall()
    c.close()

    sig = []
    for ts, sym, data in rows:
        o = json.loads(data); det = o.get("detail") or {}
        if det.get("meta_r") is None or sym not in MAP:
            continue
        sig.append({"ts": pd.Timestamp(ts), "sym": sym, "bias": o.get("bias"),
                    "meta_r": det["meta_r"], "thr": det.get("threshold"),
                    "passed": bool(det.get("pass"))})
    print(f"scored signals on record: {len(sig)}")

    cache = {}
    def bars(s):
        if s not in cache:
            d = pd.DataFrame(mt5.copy_rates_range(
                MAP[s], mt5.TIMEFRAME_M5,
                pd.Timestamp("2026-08-11"), pd.Timestamp("2026-08-15")))
            d["dt"] = pd.to_datetime(d["time"], unit="s")
            prev = d.close.shift(1)
            tr = pd.concat([d.high - d.low, (d.high - prev).abs(),
                            (d.low - prev).abs()], axis=1).max(axis=1)
            d["atr"] = tr.rolling(14).mean()
            cache[s] = d
        return cache[s]

    out = []
    for s in sig:
        if not s["bias"]:
            continue
        d = bars(s["sym"])
        idx = d.index[d.dt <= s["ts"]]
        if len(idx) == 0:
            continue
        i = idx[-1]
        atr = d.atr.iloc[i]
        if not atr or np.isnan(atr) or atr <= 0:
            continue
        entry = float(d.close.iloc[i]); buy = s["bias"] == "buy"
        risk = 2.5 * atr
        tp = entry + risk * 2.5 if buy else entry - risk * 2.5
        R = run_exit(d.high.values, d.low.values, i + 1, buy, entry, risk, tp)
        if R is None:
            continue
        out.append({**s, "R": R})
    mt5.shutdown()

    df = pd.DataFrame(out)
    if df.empty:
        print("no replayable signals"); return
    print(f"replayed: {len(df)}\n")

    p, b = df[df.passed], df[~df.passed]
    print("=== was the gate right? ===")
    for lbl, g in (("PASSED (traded)", p), ("BLOCKED", b)):
        if len(g) == 0: continue
        print(f"  {lbl:18s} n={len(g):>4}  avg {g.R.mean():>+7.3f}R  "
              f"win {(g.R > 0.05).mean()*100:>5.1f}%  total {g.R.sum():>+8.1f}R")
    if len(p) and len(b):
        print(f"\n  gate is {'RIGHT' if p.R.mean() > b.R.mean() else 'WRONG'}: "
              f"passed {p.R.mean():+.3f}R vs blocked {b.R.mean():+.3f}R "
              f"(edge {p.R.mean()-b.R.mean():+.3f}R)")

    print("\n=== outcome by score decile — does the score actually rank? ===")
    df["dec"] = pd.qcut(df.meta_r, 10, labels=False, duplicates="drop")
    print(f"  {'decile':>7} {'score range':>20} {'n':>5} {'avg R':>9} {'win%':>7}")
    for dcl, g in df.groupby("dec"):
        print(f"  {int(dcl)+1:>7} {g.meta_r.min():>9.3f}..{g.meta_r.max():<9.3f} "
              f"{len(g):>5} {g.R.mean():>+9.3f} {(g.R > 0.05).mean()*100:>6.1f}%")

    print("\n=== choosing a threshold ===")
    print(f"  {'threshold':>10} {'kept':>6} {'keep%':>7} {'avg R':>9} {'total R':>9}")
    for thr in (0.0, 0.10, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.5155):
        k = df[df.meta_r >= thr]
        if len(k) < 5: continue
        star = "  <- current" if abs(thr - 0.5155) < 1e-6 else ""
        print(f"  {thr:>10.4f} {len(k):>6} {len(k)/len(df)*100:>6.1f}% "
              f"{k.R.mean():>+9.3f} {k.R.sum():>+9.1f}{star}")


if __name__ == "__main__":
    main()
