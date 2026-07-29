"""Stress-test the gold LSTM 'beat': 5 runs, varied seeds + time-splits.
Real edge survives all 5; noise scatters around zero. Reuses build_lstm's
deterministic data pipeline (built once, cached)."""
import sys, warnings
from datetime import timedelta
import numpy as np
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import build_lstm as B
RR = 2.5

print("Building gold sequences once...", flush=True)
X, y, meta = B.build_symbol("XAUUSD")
print(f"gold samples: {len(X)}\n", flush=True)


def expect(labels):
    if len(labels) == 0: return float("nan")
    wr = float(np.mean(labels)); return wr * RR - (1 - wr)


def dedupe(meta_df, idx):
    sub = meta_df.iloc[idx].sort_values(["bias", "ts"]); keep, last = [], {}
    for i, ts, b in zip(sub.index, sub.ts, sub.bias):
        if b not in last or (ts - last[b]) > timedelta(minutes=45): keep.append(i)
        last[b] = ts
    return keep


def one_run(seed, split_frac):
    import torch, torch.nn as nn
    torch.manual_seed(seed); np.random.seed(seed)
    n = len(X); cut = int(n * split_frac)
    mu = X[:cut].reshape(-1, X.shape[2]).mean(0); sd = X[:cut].reshape(-1, X.shape[2]).std(0) + 1e-6
    Xn = (X - mu) / sd
    vcut = int(cut * 0.85)
    Xtr, ytr = torch.tensor(Xn[:vcut]), torch.tensor(y[:vcut])
    Xval, yval = torch.tensor(Xn[vcut:cut]), torch.tensor(y[vcut:cut])
    Xte = torch.tensor(Xn[cut:]); yte = y[cut:]; meta_te = meta.iloc[cut:].reset_index(drop=True)

    class Net(nn.Module):
        def __init__(s, f, hid=48):
            super().__init__(); s.lstm = nn.LSTM(f, hid, batch_first=True)
            s.drop = nn.Dropout(0.3); s.fc = nn.Linear(hid, 1)
        def forward(s, x): o, _ = s.lstm(x); return s.fc(s.drop(o[:, -1, :])).squeeze(-1)

    net = Net(X.shape[2])
    pw = torch.tensor([(ytr == 0).sum() / max((ytr == 1).sum(), 1)], dtype=torch.float32)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pw); opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
    best = 1e9; best_state = None; pat = 0
    for ep in range(25):
        net.train(); perm = torch.randperm(len(Xtr))
        for b in range(0, len(perm), 256):
            idx = perm[b:b+256]; opt.zero_grad(); lossf(net(Xtr[idx]), ytr[idx]).backward(); opt.step()
        net.eval()
        with torch.no_grad(): vl = lossf(net(Xval), yval).item()
        if vl < best - 1e-4: best = vl; best_state = {k: v.clone() for k, v in net.state_dict().items()}; pat = 0
        else:
            pat += 1
            if pat >= 5: break
    if best_state: net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad(): p = torch.sigmoid(net(Xte)).numpy()
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(yte, p) if len(np.unique(yte)) > 1 else float("nan")
    rule_idx = np.where((meta_te.should_scalp.values == 1) & (meta_te.align4h.values == 1))[0]
    k = len(rule_idx); model_idx = np.argsort(-p)[:k]
    r = expect(yte[dedupe(meta_te, rule_idx)]); m = expect(yte[dedupe(meta_te, model_idx)])
    return auc, r, m


runs = [(1, 0.75), (2, 0.72), (3, 0.78), (4, 0.74), (5, 0.76)]
print(f"{'run':4}{'seed':5}{'split':7}{'AUC':>7}{'rule':>8}{'LSTM':>8}{'gap':>8}")
gaps = []
for i, (seed, sp) in enumerate(runs, 1):
    auc, r, m = one_run(seed, sp)
    gaps.append(m - r)
    print(f"{i:<4}{seed:<5}{sp:<7}{auc:>7.3f}{r:>+8.2f}{m:>+8.2f}{m-r:>+8.2f}", flush=True)

g = np.array(gaps)
print(f"\ngap mean {g.mean():+.3f}R | std {g.std():.3f}R | min {g.min():+.2f} | max {g.max():+.2f}")
print("VERDICT:", "REAL edge (consistently positive)" if (g > 0.03).all()
      else "NOISE (scatters around zero / flips sign)" if g.min() < 0
      else "INCONCLUSIVE (small + but not robust)")
