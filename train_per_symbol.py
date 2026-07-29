"""Per-symbol AI model — train + walk-forward backtest vs the simple rule.

For each symbol independently:
  - Time-split: train on first 75% of history, test on the last 25% (unseen).
  - Train a gradient-boosted model on decision-time features.
  - Backtest on the test set, comparing THREE selectors at equal trade counts:
      1. take-all            (every signal)
      2. RULE baseline       (engine passes + WITH the 4H trend)
      3. MODEL               (top signals by predicted win-prob, matched to
                              the rule's trade frequency)
  - Report win-rate AND expectancy(R), raw and episode-deduped (45 min).

Honest bar: the MODEL must beat the RULE on out-of-sample expectancy to be
worth deploying. Win rate is secondary — expectancy(R) at RR 2.5 is what pays.
"""
import sqlite3
import sys
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
RR = 2.5
BREAKEVEN = 1 / (1 + RR)          # 28.6% WR breakeven at RR 2.5
MIN_TEST = 300                    # need enough test samples to trust a symbol

FEATURES = ["score", "should_scalp", "lg_strength", "lg_wick", "lg_sweep",
            "had_cisd", "had_choch_confirm", "had_ob", "had_fvg", "ema50_aligned",
            "rsi", "stoch", "pd_pos", "adx", "atr_rel",
            "hour", "hour_sin", "hour_cos", "dow",
            "align4h", "is_buy", "veto_ct", "veto_pd", "mom_proxy"]


def load(symbol):
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT * FROM samples WHERE symbol=? AND label IS NOT NULL ORDER BY ts",
        conn, params=(symbol,))
    conn.close()
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"])
    df["atr_rel"] = (df["atr"] / df["entry"]).replace([np.inf, -np.inf], 0).fillna(0)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["is_buy"] = (df["bias"] == "buy").astype(int)
    df["lg_wick"] = (df["lg_pattern"] == "wick_rejection").astype(int)
    df["lg_sweep"] = (df["lg_pattern"] == "sweep_recovery").astype(int)
    df["veto_ct"] = (df["veto"] == "counter_trend").astype(int)
    df["veto_pd"] = (df["veto"] == "pd").astype(int)
    a = np.zeros(len(df), dtype=int)
    a[((df.bias == "buy") & (df.macro4h == "bullish")).values] = 1
    a[((df.bias == "sell") & (df.macro4h == "bearish")).values] = 1
    a[((df.bias == "buy") & (df.macro4h == "bearish")).values] = -1
    a[((df.bias == "sell") & (df.macro4h == "bullish")).values] = -1
    df["align4h"] = a
    # crude momentum-vs-zone proxy: discount buys / premium sells = with pullback
    df["mom_proxy"] = np.where(df.is_buy == 1, 50 - df.pd_pos.fillna(50),
                               df.pd_pos.fillna(50) - 50)
    for c in FEATURES:
        if c in df:
            df[c] = df[c].fillna(0)
    return df


def dedupe(d):
    d = d.sort_values(["bias", "ts"])
    keep, last = [], {}
    for idx, ts, bias in zip(d.index, d.ts, d.bias):
        if bias not in last or (ts - last[bias]) > timedelta(minutes=45):
            keep.append(idx)
        last[bias] = ts
    return d.loc[keep]


def stats(d):
    if len(d) == 0:
        return 0, float("nan"), float("nan")
    wr = d.label.mean()
    return len(d), wr, wr * RR - (1 - wr)


def line(tag, d, per_day):
    n, wr, exp = stats(d)
    r = dedupe(d)
    nn, wwr, eexp = stats(r)
    return (f"  {tag:26} raw n={n:>5} WR={wr*100:>4.1f}% exp={exp:>+5.2f}R  |  "
            f"dedup n={nn:>4} ({nn/per_day:>4.1f}/day) WR={wwr*100:>4.1f}% exp={eexp:>+5.2f}R")


def run(symbol):
    df = load(symbol)
    if df.empty:
        print(f"\n### {symbol}: no data yet"); return None
    split = df.ts.quantile(0.75)
    tr, te = df[df.ts <= split], df[df.ts > split].copy()
    if len(te) < MIN_TEST:
        print(f"\n### {symbol}: only {len(te)} test samples (<{MIN_TEST}) — skip"); return None
    days = max((te.ts.max() - te.ts.min()).days, 1)

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    m = HistGradientBoostingClassifier(max_iter=350, learning_rate=0.06, max_depth=4,
                                       min_samples_leaf=150, l2_regularization=1.0,
                                       early_stopping=True, validation_fraction=0.15,
                                       random_state=42)
    m.fit(tr[FEATURES], tr.label)
    te["p"] = m.predict_proba(te[FEATURES])[:, 1]
    auc = roc_auc_score(te.label, te.p)

    rule = te[(te.should_scalp == 1) & (te.align4h == 1)]
    n_rule = len(rule)
    thr = np.sort(te.p.values)[-n_rule] if 0 < n_rule <= len(te) else te.p.median()
    model = te[te.p >= thr]

    print(f"\n### {symbol}  (train {len(tr)}, test {len(te)}, {days}d unseen, AUC {auc:.3f})")
    print(line("take-ALL", te, days))
    print(line("RULE (pass+WITH-4H)", rule, days))
    print(line("MODEL (=rule freq)", model, days))
    _, _, r_exp = stats(dedupe(rule))
    _, _, m_exp = stats(dedupe(model))
    verdict = ("MODEL BEATS rule" if m_exp > r_exp + 0.03 else
               "model ~= rule (no gain)" if abs(m_exp - r_exp) <= 0.03 else
               "MODEL WORSE than rule")
    print(f"  -> VERDICT: {verdict}  (model {m_exp:+.2f}R vs rule {r_exp:+.2f}R, deduped)")
    return (symbol, r_exp, m_exp, verdict)


if __name__ == "__main__":
    print(f"RR {RR} | breakeven WR {BREAKEVEN*100:.1f}% | walk-forward 75/25 by time")
    out = []
    for s in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
        r = run(s)
        if r:
            out.append(r)
    print("\n" + "=" * 60 + "\nSUMMARY")
    for sym, re_, me, v in out:
        print(f"  {sym:8} rule {re_:+.2f}R -> model {me:+.2f}R   {v}")
