"""Feedforward neural network (MLP) test — per symbol, walk-forward.

Third model class: trees (gradient boost) and sequences (LSTM) both failed
to beat the rule. This is a dense neural net on the same snapshot features.
Same honest bar: model selection (top by win-prob, matched to rule count)
must beat the rule's out-of-sample expectancy(R) to deploy.
Reuses train_per_symbol's tested data pipeline for an apples-to-apples test.
"""
import sys, warnings
from datetime import timedelta
import numpy as np
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from train_per_symbol import load, dedupe, FEATURES, RR


def expect(labels):
    if len(labels) == 0: return float("nan")
    wr = float(np.mean(labels)); return wr * RR - (1 - wr)


def run(symbol):
    import torch, torch.nn as nn
    torch.manual_seed(42); np.random.seed(42)
    df = load(symbol)
    if df.empty:
        print(f"\n### {symbol}: no data"); return None
    split = df.ts.quantile(0.75)
    tr, te = df[df.ts <= split].copy(), df[df.ts > split].copy()
    if len(te) < 300:
        print(f"\n### {symbol}: too few test ({len(te)})"); return None

    Xtr = tr[FEATURES].values.astype(np.float32)
    Xte = te[FEATURES].values.astype(np.float32)
    ytr = tr.label.values.astype(np.float32)
    yte = te.label.values.astype(np.float32)
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-6
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    vcut = int(len(Xtr) * 0.85)                       # time-ordered inner val
    Xv, yv = Xtr[vcut:], ytr[vcut:]
    Xt, yt = Xtr[:vcut], ytr[:vcut]

    class MLP(nn.Module):
        def __init__(s, f):
            super().__init__()
            s.net = nn.Sequential(
                nn.Linear(f, 64), nn.BatchNorm1d(64), nn.ReLU(), nn.Dropout(0.3),
                nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.2),
                nn.Linear(32, 1))
        def forward(s, x): return s.net(x).squeeze(-1)

    net = MLP(len(FEATURES))
    pw = torch.tensor([(yt == 0).sum() / max((yt == 1).sum(), 1)], dtype=torch.float32)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pw)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    Xt_t, yt_t = torch.tensor(Xt), torch.tensor(yt)
    Xv_t, yv_t = torch.tensor(Xv), torch.tensor(yv)
    best, best_state, pat = 1e9, None, 0
    for ep in range(60):
        net.train(); perm = torch.randperm(len(Xt_t))
        for b in range(0, len(perm), 256):
            idx = perm[b:b+256]
            if len(idx) < 2: continue
            opt.zero_grad(); lossf(net(Xt_t[idx]), yt_t[idx]).backward(); opt.step()
        net.eval()
        with torch.no_grad(): vl = lossf(net(Xv_t), yv_t).item()
        if vl < best - 1e-4: best, best_state, pat = vl, {k: v.clone() for k, v in net.state_dict().items()}, 0
        else:
            pat += 1
            if pat >= 6: break
    if best_state: net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad(): p = torch.sigmoid(net(torch.tensor(Xte))).numpy()
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(yte, p) if len(np.unique(yte)) > 1 else float("nan")

    te = te.reset_index(drop=True)
    rule_idx = np.where((te.should_scalp.values == 1) & (te.align4h.values == 1))[0]
    k = len(rule_idx); model_idx = np.argsort(-p)[:k] if 0 < k <= len(p) else np.arange(len(p))
    days = max((te.ts.max() - te.ts.min()).days, 1)

    def rep(tag, idx):
        _, wr = len(idx), (np.mean(yte[idx]) if len(idx) else float("nan"))
        d = te.iloc[idx].sort_values(["bias", "ts"]); keep, last = [], {}
        for ii, ts, bb in zip(d.index, d.ts, d.bias):
            if bb not in last or (ts - last[bb]) > timedelta(minutes=45): keep.append(ii)
            last[bb] = ts
        dl = te.label.values[keep]
        dwr = np.mean(dl) if len(dl) else float("nan"); dex = expect(dl)
        print(f"  {tag:22} raw n={len(idx):>5} WR={wr*100:>4.1f}% exp={expect(yte[idx]):>+5.2f}R | "
              f"dedup n={len(keep):>4} ({len(keep)/days:>4.1f}/d) WR={dwr*100:>4.1f}% exp={dex:>+5.2f}R")
        return dex

    print(f"\n### {symbol}  (train {vcut}, test {len(te)}, {days}d unseen, MLP AUC {auc:.3f})")
    rep("take-ALL", np.arange(len(p)))
    r = rep("RULE (pass+WITH-4H)", rule_idx)
    m = rep("MLP (=rule freq)", model_idx)
    v = ("MLP BEATS rule" if m > r + 0.03 else "MLP ~= rule (no gain)" if abs(m - r) <= 0.03 else "MLP WORSE than rule")
    print(f"  -> VERDICT: {v}  (MLP {m:+.2f}R vs rule {r:+.2f}R, deduped)")
    return (symbol, r, m, v)


if __name__ == "__main__":
    print(f"MLP (dense NN) | RR {RR} | walk-forward 75/25 | must beat rule")
    out = [r for r in (run(s) for s in ["BTC", "ETH", "XAUUSD", "XAGUSD"]) if r]
    print("\n" + "=" * 56 + "\nSUMMARY (deduped, out-of-sample)")
    for sym, r, m, v in out:
        print(f"  {sym:8} rule {r:+.2f}R -> MLP {m:+.2f}R   {v}")
