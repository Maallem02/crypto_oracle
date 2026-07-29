"""Final rigor: does the BTC/ETH MLP edge survive DIFFERENT time-splits
(different test periods/regimes), not just different seeds? Robust to BOTH
axes => real enough to justify a live shadow-test.
"""
import sys, warnings
from datetime import timedelta
import numpy as np
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from train_per_symbol import load, FEATURES, RR
from verify_mlp import expect, dedup_labels


def prep_at(df, frac):
    split = df.ts.quantile(frac)
    tr, te = df[df.ts <= split].copy(), df[df.ts > split].copy().reset_index(drop=True)
    if len(te) < 300:
        return None
    Xtr = tr[FEATURES].values.astype(np.float32); Xte = te[FEATURES].values.astype(np.float32)
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-6
    return (Xtr - mu) / sd, tr.label.values.astype(np.float32), (Xte - mu) / sd, te


def one(Xtr, ytr, Xte, te, seed):
    import torch, torch.nn as nn
    torch.manual_seed(seed); np.random.seed(seed)
    vcut = int(len(Xtr) * 0.85)
    Xt, yt, Xv, yv = Xtr[:vcut], ytr[:vcut], Xtr[vcut:], ytr[vcut:]

    class MLP(nn.Module):
        def __init__(s, f):
            super().__init__()
            s.net = nn.Sequential(nn.Linear(f, 64), nn.BatchNorm1d(64), nn.ReLU(), nn.Dropout(0.3),
                                  nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.2), nn.Linear(32, 1))
        def forward(s, x): return s.net(x).squeeze(-1)

    net = MLP(Xtr.shape[1])
    pw = torch.tensor([(yt == 0).sum() / max((yt == 1).sum(), 1)], dtype=torch.float32)
    lf = nn.BCEWithLogitsLoss(pos_weight=pw); opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    Xt_t, yt_t, Xv_t, yv_t = torch.tensor(Xt), torch.tensor(yt), torch.tensor(Xv), torch.tensor(yv)
    best, bs, pat = 1e9, None, 0
    for ep in range(60):
        net.train(); perm = torch.randperm(len(Xt_t))
        for b in range(0, len(perm), 256):
            idx = perm[b:b+256]
            if len(idx) < 2: continue
            opt.zero_grad(); lf(net(Xt_t[idx]), yt_t[idx]).backward(); opt.step()
        net.eval()
        with torch.no_grad(): vl = lf(net(Xv_t), yv_t).item()
        if vl < best - 1e-4: best, bs, pat = vl, {k: v.clone() for k, v in net.state_dict().items()}, 0
        else:
            pat += 1
            if pat >= 6: break
    if bs: net.load_state_dict(bs)
    net.eval()
    with torch.no_grad(): p = torch.sigmoid(net(torch.tensor(Xte))).numpy()
    rule_idx = np.where((te.should_scalp.values == 1) & (te.align4h.values == 1))[0]
    k = len(rule_idx); midx = np.argsort(-p)[:k]
    return expect(dedup_labels(te, midx)) - expect(dedup_labels(te, rule_idx))


SPLITS = [0.65, 0.70, 0.75, 0.80, 0.85]
SEEDS = [1, 2]
print("MLP split-robustness | splits x seeds | gap = MLP - rule (deduped R)")
for sym in ["BTC", "ETH"]:
    df = load(sym)
    print(f"\n### {sym}")
    allg = []
    for fr in SPLITS:
        d = prep_at(df, fr)
        if d is None:
            print(f"  split {fr}: too few test"); continue
        gs = [one(*d, s) for s in SEEDS]
        allg += gs
        print(f"  split {fr}: gaps {[round(x,2) for x in gs]}  mean {np.mean(gs):+.2f}R")
    g = np.array(allg)
    v = ("ROBUST across splits+seeds" if (g > 0).all() and g.mean() > 0.03
         else "MOSTLY + (some weak)" if g.mean() > 0.03 and (g > -0.03).mean() > 0.8
         else "NOT robust (regime/seed dependent)")
    print(f"  => {sym}: mean {g.mean():+.2f}R | min {g.min():+.2f} | {(g>0).sum()}/{len(g)} positive | {v}")
