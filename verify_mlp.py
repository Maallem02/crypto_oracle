"""Stress-test the MLP 'beat': every symbol, 5 seeds, fixed walk-forward split.
A real edge stays positive across seeds; seed-luck scatters/flips.
"""
import sys, warnings
from datetime import timedelta
import numpy as np
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from train_per_symbol import load, FEATURES, RR


def expect(labels):
    if len(labels) == 0: return float("nan")
    wr = float(np.mean(labels)); return wr * RR - (1 - wr)


def dedup_labels(te, idx):
    d = te.iloc[idx].sort_values(["bias", "ts"]); keep, last = [], {}
    for ii, ts, bb in zip(d.index, d.ts, d.bias):
        if bb not in last or (ts - last[bb]) > timedelta(minutes=45): keep.append(ii)
        last[bb] = ts
    return te.label.values[keep]


def prep(symbol):
    df = load(symbol)
    if df.empty or (df.ts > df.ts.quantile(0.75)).sum() < 300:
        return None
    split = df.ts.quantile(0.75)
    tr, te = df[df.ts <= split].copy(), df[df.ts > split].copy().reset_index(drop=True)
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
    yte = te.label.values.astype(np.float32)
    rule_idx = np.where((te.should_scalp.values == 1) & (te.align4h.values == 1))[0]
    k = len(rule_idx); midx = np.argsort(-p)[:k]
    return expect(dedup_labels(te, midx)) - expect(dedup_labels(te, rule_idx))


SEEDS = [1, 2, 3, 4, 5]
print(f"MLP multi-seed verification | 5 seeds | fixed 75/25 split | gap = MLP - rule (deduped R)")
print(f"{'symbol':8}{'seed gaps':40}{'mean':>7}{'min':>7}  verdict")
summary = []
for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
    d = prep(sym)
    if d is None:
        print(f"{sym:8} no data"); continue
    gaps = [one(*d, s) for s in SEEDS]
    g = np.array(gaps)
    v = ("ROBUST +" if (g > 0.03).all() else "NOISE (flips sign)" if g.min() < -0.02 else "weak/inconclusive")
    print(f"{sym:8}{str([round(x,2) for x in gaps]):40}{g.mean():>+7.2f}{g.min():>+7.2f}  {v}")
    summary.append((sym, g.mean(), g.min(), v))
print("\nVERDICT: MLP edge is real only where ALL seeds stay positive.")
for sym, mean, mn, v in summary:
    print(f"  {sym:8} mean {mean:+.2f}R  worst-seed {mn:+.2f}R  -> {v}")
