"""ORDER FLOW x VOLUME PROFILE RE-ENTRIES (BTC, ETH, Binance, 3 years). Read-only.

Question: does order flow (aggressive buyers vs sellers, Binance taker volume)
at the moment of a VP re-entry separate the trades that work from those that
don't — information that price-only features never had?

Rules fixed BEFORE looking at the results:
  * signals = the re-entry rows of vp_long_history_study.py (production detector,
    Exness-like spread, 2R, stop first), live-like set: room >= 1R, 1 position
    per symbol at a time
  * features, all signed so that POSITIVE = in favour of the trade:
      trg_delta    delta share of the trigger M15 bar           (2*taker - vol) / vol
      ex_delta     delta share during the excursion outside the value area
      d1h, d2h     delta share over the last 1 h / 2 h before entry
      div2h        delta over 2 h in favour while price over 2 h went against (0/1)
      ex_relvol    excursion volume vs the session's average bar
      trg_relvol   trigger bar volume vs the session's average bar
  * DISCOVERY = 04/10/2023 -> 30/09/2025, TEST = 01/10/2025 -> 07/10/2026
  * (a) single-feature terciles: cut points from discovery only; the best
        tercile of the best feature (avg R, n >= 60 in discovery) is applied
        unchanged to the test year
  * (b) gradient boosting on order flow + geometry, trained on discovery only,
        keeps the top 30 % / 50 % of predicted win probability in the test year
  * SUCCESS = in the test year, live-like avg >= +0.10R per trade, >= 100
        trades, and positive in BOTH halves of the test year."""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vp_long_history_study as L

UTC = timezone.utc
ROWS = os.path.join(HERE, "data", "binance_1m", "vp_long_rows.json")
TEST0 = datetime(2025, 10, 1, tzinfo=UTC).timestamp()
TEST_MID = datetime(2026, 4, 1, tzinfo=UTC).timestamp()
rng = np.random.default_rng(3)

D = json.load(open(ROWS))
R = [r for r in D["rows"] if r["setup"] == "va_reversal"]
print(f"{len(R)} re-entry signals ({D['weight']} weighting)")

# ── order-flow features from Binance 1-minute taker volume ───────────────────
for name, pair in L.PAIRS.items():
    m1 = L.load(pair)
    t = m1["time"]
    cv = np.r_[0.0, np.cumsum(m1["vol"])]
    ct = np.r_[0.0, np.cumsum(m1["taker"])]

    def win(a, b):
        i, j = np.searchsorted(t, a), np.searchsorted(t, b)
        return cv[j] - cv[i], ct[j] - ct[i], i, j

    def delta(a, b):
        v, tk, _, _ = win(a, b)
        return (2 * tk - v) / v if v > 0 else 0.0

    for r in R:
        if r["sym"] != name:
            continue
        sg = 1 if r["dir"] == "buy" else -1
        e = r["t"]                                   # entry = close of the trigger bar
        n_out = r["ctx"].get("n_out", 1)
        f = {}
        f["trg_delta"] = sg * delta(e - 900, e)
        f["ex_delta"] = sg * delta(e - 900 - 900 * n_out, e - 900)
        f["d1h"] = sg * delta(e - 3600, e)
        f["d2h"] = sg * delta(e - 7200, e)
        i0, i1 = np.searchsorted(t, e - 7200), np.searchsorted(t, e) - 1
        p2h = sg * (m1["close"][i1] - m1["close"][i0]) if i1 > i0 else 0.0
        f["div2h"] = 1.0 if (f["d2h"] > 0 and p2h < 0) else 0.0
        day0 = e - e % 86400
        vday, _, i, j = win(day0, e)
        avg_bar = vday / max((j - i) / 15, 1)        # average M15 bar volume so far today
        v_ex, _, _, _ = win(e - 900 - 900 * n_out, e - 900)
        v_tr, _, _, _ = win(e - 900, e)
        f["ex_relvol"] = (v_ex / max(n_out, 1)) / avg_bar if avg_bar > 0 else 1.0
        f["trg_relvol"] = v_tr / avg_bar if avg_bar > 0 else 1.0
        r["of"] = f
    print(f"  {name}: order-flow features computed")


def take(rows):
    busy, out = {}, []
    for r in sorted(rows, key=lambda r: r["t"]):
        if busy.get(r["sym"], 0) > r["t"]:
            continue
        busy[r["sym"]] = r["x2"]
        out.append(r)
    return out


def st(rows):
    if not rows:
        return "n=   0"
    x = np.array([r["r2"] for r in rows])
    boot = [rng.choice(x, len(x)).mean() for _ in range(1500)]
    gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
    return (f"n={len(x):4d} WR {100 * (x > 0).mean():3.0f}% avg {x.mean():+.3f}R "
            f"[90% CI {np.percentile(boot, 5):+.3f}..{np.percentile(boot, 95):+.3f}] sum {x.sum():+6.1f}R "
            f"PF {gp / gl if gl else 9:4.2f}")


base = [r for r in R if r["room"] >= 1.0 and "of" in r]
disc = [r for r in base if r["t"] < TEST0]
test = [r for r in base if r["t"] >= TEST0]
FEATS = ["trg_delta", "ex_delta", "d1h", "d2h", "ex_relvol", "trg_relvol"]

print("\n=== BASELINE (live-like, no order-flow filter) ===")
print(f"  discovery  {st(take(disc))}")
print(f"  test year  {st(take(test))}")

print("\n=== (a) SINGLE FEATURES — terciles cut on discovery, every signal (no position limit) ===")
best = None
for f in FEATS:
    v = np.array([r["of"][f] for r in disc])
    q1, q2 = np.percentile(v, [33.3, 66.7])
    lab = lambda x: 0 if x < q1 else (1 if x < q2 else 2)
    print(f"  {f:10s} cuts {q1:+.3f} / {q2:+.3f}")
    for k, name in enumerate(("low", "mid", "high")):
        d_ = [r for r in disc if lab(r["of"][f]) == k]
        t_ = [r for r in test if lab(r["of"][f]) == k]
        print(f"    {name:4s}  discovery {st(d_)}")
        print(f"          test      {st(t_)}")
        if len(d_) >= 60:
            m = np.mean([r["r2"] for r in d_])
            if best is None or m > best[0]:
                best = (m, f, k, q1, q2)
for flag in (0.0, 1.0):
    d_ = [r for r in disc if r["of"]["div2h"] == flag]
    t_ = [r for r in test if r["of"]["div2h"] == flag]
    print(f"  div2h={int(flag)}  discovery {st(d_)}\n           test      {st(t_)}")

m, f, k, q1, q2 = best
sel = lambda r: (0 if r["of"][f] < q1 else (1 if r["of"][f] < q2 else 2)) == k
print(f"\n  BEST ON DISCOVERY: {f} tercile {('low', 'mid', 'high')[k]} (discovery avg {m:+.3f}R)")
tt = take([r for r in test if sel(r)])
print(f"  applied to the test year, live-like: {st(tt)}")
print(f"    test halves: {sum(r['r2'] for r in tt if r['t'] < TEST_MID):+.1f}R / "
      f"{sum(r['r2'] for r in tt if r['t'] >= TEST_MID):+.1f}R")

print("\n=== (b) GRADIENT BOOSTING, trained on discovery only ===")
from sklearn.ensemble import HistGradientBoostingClassifier

GEOM = ["risk_atr", "va_w", "to_poc", "n_out", "depth", "hour"]
cols = FEATS + ["div2h"] + GEOM


def X(rows):
    return np.array([[r["of"].get(c, r["ctx"].get(c, np.nan)) if c in r["of"] else r["ctx"].get(c, np.nan)
                      for c in cols] for r in rows], float)


ytr = np.array([r["r2"] > 0 for r in disc])
for label, use in (("order flow only", FEATS + ["div2h"]), ("order flow + geometry", cols)):
    idx = [cols.index(c) for c in use]
    clf = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05,
                                         min_samples_leaf=40, random_state=0)
    clf.fit(X(disc)[:, idx], ytr)
    p = clf.predict_proba(X(test)[:, idx])[:, 1]
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score([r["r2"] > 0 for r in test], p)
    print(f"  {label:24s} test AUC {auc:.3f}  (0.5 = no information)")
    for keep in (0.3, 0.5):
        cut = np.quantile(p, 1 - keep)
        chosen = take([r for r, pi in zip(test, p) if pi >= cut])
        h1 = sum(r["r2"] for r in chosen if r["t"] < TEST_MID)
        h2 = sum(r["r2"] for r in chosen if r["t"] >= TEST_MID)
        ok = (np.mean([r["r2"] for r in chosen]) >= 0.10 and len(chosen) >= 100 and h1 > 0 and h2 > 0) if chosen else False
        print(f"    keep top {int(keep * 100)}%: {st(chosen)} | halves {h1:+.1f} / {h2:+.1f}  -> "
              f"{'PASSES' if ok else 'fails'} the success bar")
