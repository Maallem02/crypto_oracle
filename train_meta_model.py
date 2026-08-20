"""
Train and persist the meta-model gate.

Run:  python train_meta_model.py

Writes meta_model.joblib containing the model, the exact feature list, and the
score threshold that keeps the top KEEP_PCT% of training signals. The runtime
(features/trading/meta_gate.py) refuses to load a model whose feature list
does not match its own, so a change here cannot silently feed the live gate a
differently-shaped vector.

Feature computation is IMPORTED from meta_gate, never reimplemented — the
whole failure mode this guards against is training on one definition of
"efficiency ratio" and scoring on another.
"""
import sqlite3, warnings, importlib.util
from datetime import datetime

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

warnings.filterwarnings("ignore")

from features.trading.meta_gate import (
    FEATURE_ORDER, MODEL_PATH, MIN_BARS, regime_series, regime_at,
)

_s = importlib.util.spec_from_file_location("tt", "trail_trigger_study.py")
tt = importlib.util.module_from_spec(_s); _s.loader.exec_module(tt)

SYMS = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
FROM, TO = pd.Timestamp("2026-01-09"), pd.Timestamp("2026-07-24")
KEEP_PCT = 10          # gate keeps roughly this % of signals
TRAIL_R  = 1.0         # live exit policy
BE_R     = 1.5


def build() -> pd.DataFrame:
    gates = pd.read_csv("gate_study_rows.csv")
    gates["ts"] = pd.to_datetime(gates.ts); gates = gates[gates.b_ok]
    conn = sqlite3.connect("offline_dataset.sqlite")
    rows = []
    for sym, msym in SYMS.items():
        s = pd.read_sql_query(
            "SELECT ts,symbol,bias,score,lg_pattern,lg_strength,had_cisd,had_choch_confirm,"
            "had_ob,had_fvg,ema50_aligned,rsi,stoch,pd_pos,adx,atr,macro4h,entry,risk "
            "FROM samples WHERE symbol=? AND should_scalp=1 AND r_outcome IS NOT NULL ORDER BY ts",
            conn, params=(sym,))
        if s.empty:
            continue
        s["ts"] = pd.to_datetime(s["ts"])
        s = s.merge(gates[["ts", "symbol", "bias"]], on=["ts", "symbol", "bias"])
        need = np.where(s.bias == "buy", "bullish", "bearish")
        s = s[(s.macro4h == "neutral") | (s.macro4h == need)]        # 4H filter, as live

        m5 = pd.DataFrame(mt5.copy_rates_range(msym, mt5.TIMEFRAME_M5, FROM, TO))
        if m5.empty:
            continue
        m5["dt"] = pd.to_datetime(m5["time"], unit="s")
        pos = {pd.Timestamp(t): i for i, t in enumerate(m5.dt.values)}
        ser = regime_series(m5.high.values, m5.low.values, m5.close.values)
        H, L = m5.high.values, m5.low.values

        for r in s.itertuples(index=False):
            i = pos.get(r.ts)
            if i is None or i < MIN_BARS or r.risk <= 0 or not r.atr or r.atr <= 0:
                continue
            reg = regime_at(ser, i)
            if reg is None:
                continue
            buy = r.bias == "buy"
            tp = r.entry + r.risk * 2.5 if buy else r.entry - r.risk * 2.5
            R = tt.run_exit(H, L, i + 1, buy, float(r.entry), float(r.risk),
                            tp, trail_r=TRAIL_R)
            if R is None:
                continue
            rows.append({
                "ts": r.ts, "symbol": sym, "R": R,
                "score": r.score, "lg_strength": r.lg_strength,
                "wick": 1 if r.lg_pattern == "wick_rejection" else 0,
                "cisd": r.had_cisd, "choch": r.had_choch_confirm,
                "ob": r.had_ob, "fvg": r.had_fvg, "ema50": r.ema50_aligned,
                "rsi": r.rsi, "stoch": r.stoch, "pd_pos": r.pd_pos, "adx": r.adx,
                "is_buy": 1 if buy else 0,
                "m4_neutral": 1 if r.macro4h == "neutral" else 0,
                **reg,
            })
    conn.close()
    if not rows:
        raise SystemExit("no training rows built")
    return pd.DataFrame(rows).dropna().sort_values("ts").reset_index(drop=True)


def main():
    if not mt5.initialize(timeout=20000):
        raise SystemExit("mt5 attach failed")
    d = build()
    mt5.shutdown()
    print(f"training rows: {len(d):,}  ({d.ts.min().date()} -> {d.ts.max().date()})")

    from sklearn.ensemble import HistGradientBoostingRegressor

    # Honest check before persisting anything: hold out the last 25% and
    # confirm the gate still beats taking everything on data it never saw.
    cut = int(len(d) * 0.75)
    tr, te = d.iloc[:cut], d.iloc[cut:]
    preds = []
    for seed in range(3):
        m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_depth=4,
                                          l2_regularization=1.0, random_state=seed)
        m.fit(tr[FEATURE_ORDER], tr.R)
        preds.append(m.predict(te[FEATURE_ORDER]))
    p = np.mean(preds, axis=0)
    k = max(int(len(te) * KEEP_PCT / 100), 20)
    sel = te.R.values[np.argsort(-p)[:k]]
    print(f"\nholdout check ({len(te):,} unseen signals)")
    print(f"  take all      : {te.R.mean():+.4f} R/trade")
    print(f"  top {KEEP_PCT}%       : {sel.mean():+.4f} R/trade   gain {sel.mean()-te.R.mean():+.4f}")
    if sel.mean() <= te.R.mean():
        raise SystemExit("gate does not beat take-all on holdout — NOT saving")

    # Final model on ALL data, threshold at the KEEP_PCT percentile of its own
    # in-sample predictions. Live the bot sees one signal at a time, so it needs
    # an absolute cutoff rather than a per-batch ranking.
    final = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_depth=4,
                                          l2_regularization=1.0, random_state=0)
    final.fit(d[FEATURE_ORDER], d.R)
    thr = float(np.percentile(final.predict(d[FEATURE_ORDER]), 100 - KEEP_PCT))

    import joblib
    joblib.dump({
        "model": final,
        "features": FEATURE_ORDER,
        "threshold": thr,
        "keep_pct": KEEP_PCT,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": len(d),
        "holdout_gain": float(sel.mean() - te.R.mean()),
    }, MODEL_PATH)
    print(f"\nsaved {MODEL_PATH}")
    print(f"  threshold {thr:.4f}  (keeps ~{KEEP_PCT}% of signals)")
    print(f"  rows {len(d):,}  holdout gain {sel.mean()-te.R.mean():+.4f}R")


if __name__ == "__main__":
    main()
