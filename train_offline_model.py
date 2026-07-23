"""Phase 2 — train on the offline dataset with walk-forward validation.

Train: samples before 2026-06-01 (~4.5 months)
Test:  samples from  2026-06-01 (~6 weeks the model never sees)

Deployment metric: does selecting by model probability beat the best
rule-based cohort (engine pass + WITH-4H trend) on the SAME test period?
Evaluated raw and episode-deduped (45 min) to kill autocorrelation.
"""
import sqlite3
import sys
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
SPLIT = "2026-06-01"
RR = 2.5

conn = sqlite3.connect(DB)
df = pd.read_sql_query("SELECT * FROM samples WHERE label IS NOT NULL", conn)
conn.close()
df["ts"] = pd.to_datetime(df["ts"])
print(f"samples: {len(df)} | {df.ts.min()} -> {df.ts.max()}")

# ── Feature engineering (decision-time only) ─────────────────────────────
df["align4h"] = 0
df.loc[(df.bias == "buy") & (df.macro4h == "bullish"), "align4h"] = 1
df.loc[(df.bias == "sell") & (df.macro4h == "bearish"), "align4h"] = 1
df.loc[(df.bias == "buy") & (df.macro4h == "bearish"), "align4h"] = -1
df.loc[(df.bias == "sell") & (df.macro4h == "bullish"), "align4h"] = -1
df["atr_rel"] = df.atr / df.entry
df["hour_sin"] = np.sin(2 * np.pi * df.hour / 24)
df["hour_cos"] = np.cos(2 * np.pi * df.hour / 24)
df["is_buy"] = (df.bias == "buy").astype(int)
df["is_btc"] = (df.symbol == "BTC").astype(int)
df["veto_ct"] = (df.veto == "counter_trend").astype(int)
df["veto_pd"] = (df.veto == "pd").astype(int)
for p in ("wick_rejection", "sweep_recovery"):
    df[f"lg_{p}"] = (df.lg_pattern == p).astype(int)

FEATURES = ["score", "should_scalp", "lg_strength", "lg_wick_rejection", "lg_sweep_recovery",
            "had_cisd", "had_choch_confirm", "had_ob", "had_fvg", "ema50_aligned",
            "rsi", "stoch", "pd_pos", "adx", "atr_rel",
            "hour", "hour_sin", "hour_cos", "dow",
            "align4h", "is_buy", "is_btc", "veto_ct", "veto_pd"]

train = df[df.ts < SPLIT]
test = df[df.ts >= SPLIT].copy()
print(f"train: {len(train)} (WR {train.label.mean()*100:.1f}%) | "
      f"test: {len(test)} (WR {test.label.mean()*100:.1f}%)")

X_tr, y_tr = train[FEATURES], train.label
X_te, y_te = test[FEATURES], test.label

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

model = HistGradientBoostingClassifier(
    max_iter=400, learning_rate=0.06, max_depth=4,
    min_samples_leaf=200, l2_regularization=1.0,
    early_stopping=True, validation_fraction=0.15, random_state=42)
model.fit(X_tr, y_tr)
p_te = model.predict_proba(X_te)[:, 1]
p_tr = model.predict_proba(X_tr)[:, 1]
print(f"AUC train {roc_auc_score(y_tr, p_tr):.3f} | AUC test {roc_auc_score(y_te, p_te):.3f}")

test["p"] = p_te

def dedupe(d):
    """One row per (symbol,bias) 45-min episode — kills bar-to-bar repeats."""
    d = d.sort_values(["symbol", "bias", "ts"])
    keep = []
    last = {}
    for _, r in d.iterrows():
        k = (r.symbol, r.bias)
        if k not in last or (r.ts - last[k]) > timedelta(minutes=45):
            keep.append(r.name)
        last[k] = r.ts
    return d.loc[keep]

def report(name, sel, days):
    for tag, dd in (("raw", sel), ("deduped", dedupe(sel))):
        n = len(dd)
        if n == 0:
            print(f"  {name:34} {tag:8} n=0")
            continue
        wr = dd.label.mean()
        exp = wr * RR - (1 - wr)
        print(f"  {name:34} {tag:8} n={n:>6} ({n/days:5.1f}/day) "
              f"WR={wr*100:5.1f}% exp={exp:+.2f}R")

days = (test.ts.max() - test.ts.min()).days or 1
print(f"\n=== TEST PERIOD ({days} days it never saw) ===")
report("RULE baseline: pass + WITH-4H", test[(test.should_scalp == 1) & (test.align4h == 1)], days)
for q in (0.80, 0.90, 0.95):
    thr = np.quantile(p_te, q)
    report(f"MODEL top {int((1-q)*100)}% (p>{thr:.3f})", test[test.p > thr], days)
# model at the SAME frequency as the rule baseline
n_rule = len(test[(test.should_scalp == 1) & (test.align4h == 1)])
thr_match = np.sort(p_te)[-n_rule]
report("MODEL @ same freq as rule", test[test.p >= thr_match], days)

print("\n=== calibration on test (deciles) ===")
test["dec"] = pd.qcut(test.p, 10, labels=False, duplicates="drop")
for d_, g in test.groupby("dec"):
    print(f"  decile {d_}: n={len(g):>5} pred={g.p.mean()*100:5.1f}% actual WR={g.label.mean()*100:5.1f}%")

print("\n=== top feature importances (permutation on test sample) ===")
from sklearn.inspection import permutation_importance
sub = test.sample(min(8000, len(test)), random_state=1)
imp = permutation_importance(model, sub[FEATURES], sub.label, n_repeats=3, random_state=1)
order = np.argsort(-imp.importances_mean)
for i in order[:10]:
    print(f"  {FEATURES[i]:22} {imp.importances_mean[i]:+.4f}")

import joblib
joblib.dump({"model": model, "features": FEATURES, "trained": datetime.now().isoformat(),
             "split": SPLIT}, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_model_v1.joblib")
print("\nsaved: offline_model_v1.joblib")
