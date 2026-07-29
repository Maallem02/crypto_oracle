"""Validate the DEPLOYABLE mode: MLP as a veto-filter on rule-approved trades.
Keeps all gates + rule. Among rule-approved BTC/ETH signals, drop the MLP's
lowest-confidence bottom X%. Does keeping the top (1-X)% beat taking all?
Walk-forward, multi-seed. This is the exact mechanism we'd deploy on demo.
"""
import sys, warnings
from datetime import timedelta
import numpy as np
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from train_per_symbol import load, FEATURES, RR
from verify_mlp import dedup_labels


def expect(labels):
    if len(labels) == 0: return float("nan")
    wr = float(np.mean(labels)); return wr * RR - (1 - wr)


def train_prob(Xtr, ytr, Xte, seed):
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
    with torch.no_grad():
        import torch as t
        return t.sigmoid(net(t.tensor(Xte))).numpy()


DROP = 0.30
for sym in ["BTC", "ETH"]:
    df = load(sym)
    split = df.ts.quantile(0.75)
    tr, te = df[df.ts <= split].copy(), df[df.ts > split].copy().reset_index(drop=True)
    Xtr = tr[FEATURES].values.astype(np.float32); Xte = te[FEATURES].values.astype(np.float32)
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-6
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    ytr = tr.label.values.astype(np.float32)
    rule_pos = np.where((te.should_scalp.values == 1) & (te.align4h.values == 1))[0]
    print(f"\n### {sym}: {len(rule_pos)} rule-approved test signals")
    all_exp = expect(dedup_labels(te, rule_pos))
    keeps, thr_list = [], []
    for seed in [1, 2, 3, 4, 5]:
        p = train_prob(Xtr, ytr, Xte, seed)
        pr = p[rule_pos]
        thr = np.quantile(pr, DROP)                 # veto bottom 30% by MLP prob
        thr_list.append(thr)
        keep_idx = rule_pos[pr >= thr]
        drop_idx = rule_pos[pr < thr]
        k_exp = expect(dedup_labels(te, keep_idx))
        d_exp = expect(dedup_labels(te, drop_idx))
        keeps.append(k_exp - all_exp)
        print(f"  seed {seed}: keep-top70% exp={k_exp:+.2f}R  vetoed-bottom30% exp={d_exp:+.2f}R  "
              f"(lift {k_exp-all_exp:+.2f}R)")
    g = np.array(keeps)
    print(f"  rule(all) exp={all_exp:+.2f}R | filter lift: mean {g.mean():+.2f}R min {g.min():+.2f} "
          f"| {(g>0).sum()}/5 positive | avg prob-threshold {np.mean(thr_list):.3f}")
