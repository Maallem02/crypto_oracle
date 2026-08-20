"""
Meta-model study (2026-08-11)
=============================
Different question from build_lstm.py. That one asked "where is the market
going" from raw price and came back at AUC 0.50 — no signal.

This asks the better-posed question: given a signal MY BOT HAS ALREADY
DECIDED TO TAKE, will it work? The model never predicts the market. It ranks
the bot's own candidates using what the bot already knows about them at
decision time, and we ask whether taking only the top slice beats taking all.

Population : signals that pass the CURRENT live gates (Gate B H1-EMA + 4H)
Label      : R outcome under the CURRENT live exit policy (BE 1.5R, trail 1.0R)
Model      : gradient boosting on tabular features (not a net — this is
             tabular data with mixed types, which is where GBMs win)
Split      : time-ordered 75/25, no shuffling; test set is the unseen tail
Bar        : filtering to the top slice must beat taking everything, on the
             TEST set, by more than the seed-to-seed noise
"""
import sqlite3, warnings, importlib.util
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")
_s = importlib.util.spec_from_file_location("tt", "trail_trigger_study.py")
tt = importlib.util.module_from_spec(_s); _s.loader.exec_module(tt)

SYMS = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
FROM, TO = pd.Timestamp("2026-01-09"), pd.Timestamp("2026-07-24")
WARM = 520


def market_context(msym):
    """Regime features. Efficiency ratio and extension were the only two that
    showed a real gradient in the regime study, so they earn their place."""
    m5 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M5, FROM, TO))
    if m5.empty:
        return None
    m5["dt"] = pd.to_datetime(m5["time"], unit="s")
    c, h, l = m5.close.values, m5.high.values, m5.low.values
    prev = np.roll(c, 1); prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    atr = pd.Series(tr).rolling(14).mean().bfill().values
    absmove = pd.Series(np.abs(np.diff(c, prepend=c[0])))

    def eff(n):
        net = np.abs(c - np.roll(c, n)); net[:n] = np.nan
        tot = absmove.rolling(n).sum().values
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(tot > 0, net / tot, np.nan)

    return {
        # keyed by pd.Timestamp: the caller iterates with itertuples(), which
        # yields Timestamps, and a datetime64-keyed dict silently misses every
        # lookup (producing an empty dataset rather than an error)
        "pos": {pd.Timestamp(t): i for i, t in enumerate(m5.dt.values)},
        "h": h, "l": l, "atr": atr,
        "er48": eff(48), "er96": eff(96),
        "ema200": pd.Series(c).ewm(span=200, adjust=False).mean().values,
        "c": c,
        "atr_s": pd.Series(tr).rolling(7).mean().bfill().values,
        "atr_l": pd.Series(tr).rolling(50).mean().bfill().values,
        "volpct": pd.Series(atr).rolling(500).rank(pct=True).values,
    }


def build():
    gates = pd.read_csv("gate_study_rows.csv")
    gates["ts"] = pd.to_datetime(gates.ts); gates = gates[gates.b_ok]
    conn = sqlite3.connect("offline_dataset.sqlite")
    rows = []
    for sym, msym in SYMS.items():
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,score,lg_pattern,lg_strength,had_cisd,had_choch_confirm,"
            "had_ob,had_fvg,ema50_aligned,rsi,stoch,pd_pos,adx,atr,hour,dow,macro4h,entry,risk "
            "FROM samples WHERE symbol=? AND should_scalp=1 AND r_outcome IS NOT NULL ORDER BY ts",
            conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])
        s = s.merge(gates[["ts", "symbol", "bias"]], on=["ts", "symbol", "bias"])
        need = np.where(s.bias == "buy", "bullish", "bearish")
        s = s[(s.macro4h == "neutral") | (s.macro4h == need)]      # 4H filter, as live
        d = market_context(msym)
        if d is None:
            continue
        for r in s.itertuples(index=False):
            i = d["pos"].get(r.ts)
            if i is None or i < WARM or r.risk <= 0 or not r.atr or r.atr <= 0:
                continue
            if np.isnan(d["er96"][i]):
                continue
            buy = r.bias == "buy"
            tp = r.entry + r.risk * 2.5 if buy else r.entry - r.risk * 2.5
            out = tt.run_exit(d["h"], d["l"], i + 1, buy, float(r.entry), float(r.risk),
                              tp, trail_r=1.0)          # LIVE exit policy
            if out is None:
                continue
            rows.append({
                "ts": r.ts, "symbol": sym, "R": out,
                # ── what the bot knows at decision time ──
                "score": r.score, "lg_strength": r.lg_strength,
                "wick": 1 if r.lg_pattern == "wick_rejection" else 0,
                "cisd": r.had_cisd, "choch": r.had_choch_confirm,
                "ob": r.had_ob, "fvg": r.had_fvg, "ema50": r.ema50_aligned,
                "rsi": r.rsi, "stoch": r.stoch, "pd_pos": r.pd_pos, "adx": r.adx,
                "hour": r.hour, "dow": r.dow,
                "is_buy": 1 if buy else 0,
                "m4_neutral": 1 if r.macro4h == "neutral" else 0,
                # ── regime context ──
                "er48": d["er48"][i], "er96": d["er96"][i],
                "ext": abs(d["c"][i] - d["ema200"][i]) / d["atr"][i],
                "atr_ratio": d["atr_s"][i] / d["atr_l"][i] if d["atr_l"][i] else np.nan,
                "volpct": d["volpct"][i],
            })
    conn.close()
    if not rows:
        raise SystemExit("no samples built — check the timestamp join")
    return pd.DataFrame(rows).dropna().sort_values("ts").reset_index(drop=True)


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit("mt5 attach failed")
    d = build()
    mt5.shutdown()

    FEATS = [c for c in d.columns if c not in ("ts", "symbol", "R")]
    cut = int(len(d) * 0.75)
    tr, te = d.iloc[:cut], d.iloc[cut:]
    print(f"samples {len(d):,}   train {len(tr):,}   test {len(te):,} "
          f"({te.ts.min().date()} -> {te.ts.max().date()})")
    print(f"features: {len(FEATS)}\n")
    base = te.R.mean()
    print(f"BASELINE — take every signal the bot generates: {base:+.4f} R/trade "
          f"(n={len(te):,}, total {te.R.sum():+.0f}R)\n")

    from sklearn.ensemble import HistGradientBoostingRegressor
    print("=== does ranking by the model beat taking everything? (TEST set) ===")
    print(f"  {'keep top':>10} {'n':>6} {'avg R':>9} {'vs base':>9} {'total R':>9}   seeds(min..max)")
    per_frac = {}
    for seed in range(5):
        m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05,
                                          max_depth=4, l2_regularization=1.0,
                                          random_state=seed)
        m.fit(tr[FEATS], tr.R)
        p = m.predict(te[FEATS])
        for frac in (0.10, 0.25, 0.50, 0.75):
            k = max(int(len(te) * frac), 20)
            idx = np.argsort(-p)[:k]
            per_frac.setdefault(frac, []).append(te.R.values[idx].mean())
    for frac, vals in per_frac.items():
        k = max(int(len(te) * frac), 20)
        v = np.array(vals)
        print(f"  {frac*100:>9.0f}% {k:>6,} {v.mean():>+9.4f} {v.mean()-base:>+9.4f} "
              f"{v.mean()*k:>+9.0f}   {v.min():+.3f}..{v.max():+.3f}")

    print("\n=== is the ranking real, or seed noise? ===")
    spread = {f: max(v) - min(v) for f, v in per_frac.items()}
    for f, s in spread.items():
        gain = np.mean(per_frac[f]) - base
        verdict = "REAL" if gain > s and gain > 0.02 else "noise"
        print(f"  top {f*100:>3.0f}%: gain {gain:+.4f}R, seed spread {s:.4f}R -> {verdict}")

    print("\n=== which features does it lean on? (permutation, test set) ===")
    from sklearn.inspection import permutation_importance
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_depth=4,
                                      l2_regularization=1.0, random_state=0).fit(tr[FEATS], tr.R)
    pi = permutation_importance(m, te[FEATS], te.R, n_repeats=5, random_state=0,
                                scoring="neg_mean_squared_error")
    order = np.argsort(-pi.importances_mean)[:10]
    for i in order:
        print(f"  {FEATS[i]:14s} {pi.importances_mean[i]:+.5f} +- {pi.importances_std[i]:.5f}")


if __name__ == "__main__":
    main()
