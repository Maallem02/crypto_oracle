"""LSTM sequence model — per symbol, walk-forward, must beat the +0.2R rule.

Unlike the gradient-boost test (snapshot features), the LSTM ingests the
RAW SEQUENCE of the 32 M5 candles leading into each signal — log returns,
range/ATR, body, wick, close position. If temporal structure carries a
predictive edge, this is the tool that finds it.

Honest bar: model selection (top by predicted win-prob, matched to the
rule's trade count) must beat the rule's out-of-sample expectancy(R).
"""
import sqlite3, sys, warnings
from datetime import timedelta
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\offline_dataset.sqlite"
MT5_SYMBOLS = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm"}
SEQ_LEN = 32
RR = 2.5
EPOCHS = 25
BATCH = 256


def per_bar_features(df):
    eps = 1e-9
    c, o, h, l = df.close.values, df.open.values, df.high.values, df.low.values
    logret = np.zeros(len(c)); logret[1:] = np.log(c[1:] / np.clip(c[:-1], eps, None))
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(14).mean().bfill().values + eps
    rng = (h - l)
    feats = np.stack([
        logret,
        rng / atr,                          # volatility
        (c - o) / (rng + eps),              # body direction
        (h - np.maximum(o, c)) / (rng + eps),   # upper wick
        (c - l) / (rng + eps),              # close position in bar
    ], axis=1)
    return np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)


def build_symbol(symbol):
    import MetaTrader5 as mt5
    conn = sqlite3.connect(DB)
    s = pd.read_sql_query("SELECT ts,label,should_scalp,macro4h,bias FROM samples "
                          "WHERE symbol=? AND label IS NOT NULL ORDER BY ts",
                          conn, params=(symbol,))
    conn.close()
    if s.empty:
        return None
    s["ts"] = pd.to_datetime(s["ts"])

    if not mt5.initialize(timeout=15000):
        raise RuntimeError("mt5 init")
    from datetime import datetime
    rates = mt5.copy_rates_range(MT5_SYMBOLS[symbol], mt5.TIMEFRAME_M5,
                                 s.ts.min().to_pydatetime() - timedelta(days=1),
                                 s.ts.max().to_pydatetime() + timedelta(hours=1))
    mt5.shutdown()
    df = pd.DataFrame(rates)
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    feats = per_bar_features(df)
    pos = {t: i for i, t in enumerate(df.dt.values)}

    X, y, meta = [], [], []
    tsv = s.ts.values
    for k in range(len(s)):
        i = pos.get(tsv[k])
        if i is None or i < SEQ_LEN:
            continue
        X.append(feats[i - SEQ_LEN + 1: i + 1])
        y.append(int(s.label.iloc[k]))
        a4 = 0
        b, m = s.bias.iloc[k], s.macro4h.iloc[k]
        if (b == "buy" and m == "bullish") or (b == "sell" and m == "bearish"): a4 = 1
        elif (b == "buy" and m == "bearish") or (b == "sell" and m == "bullish"): a4 = -1
        meta.append((s.ts.iloc[k], b, int(s.should_scalp.iloc[k]), a4))
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32)
    return X, y, pd.DataFrame(meta, columns=["ts", "bias", "should_scalp", "align4h"])


def dedupe_mask(meta_df, sel_idx):
    sub = meta_df.iloc[sel_idx].sort_values(["bias", "ts"])
    keep, last = [], {}
    for idx, ts, bias in zip(sub.index, sub.ts, sub.bias):
        if bias not in last or (ts - last[bias]) > timedelta(minutes=45):
            keep.append(idx)
        last[bias] = ts
    return keep


def expect(labels):
    if len(labels) == 0: return 0, float("nan"), float("nan")
    wr = float(np.mean(labels)); return len(labels), wr, wr * RR - (1 - wr)


def run(symbol):
    import torch, torch.nn as nn
    torch.manual_seed(42); np.random.seed(42)
    data = build_symbol(symbol)
    if data is None:
        print(f"\n### {symbol}: no data"); return None
    X, y, meta = data
    n = len(X)
    cut = int(n * 0.75)
    # standardize per feature on TRAIN only
    mu = X[:cut].reshape(-1, X.shape[2]).mean(0)
    sd = X[:cut].reshape(-1, X.shape[2]).std(0) + 1e-6
    Xn = (X - mu) / sd
    vcut = int(cut * 0.85)                       # inner val slice (time-ordered)
    Xtr, ytr = Xn[:vcut], y[:vcut]
    Xval, yval = Xn[vcut:cut], y[vcut:cut]
    Xte, yte = Xn[cut:], y[cut:]
    meta_te = meta.iloc[cut:].reset_index(drop=True)

    class Net(nn.Module):
        def __init__(s, f, hid=48):
            super().__init__()
            s.lstm = nn.LSTM(f, hid, batch_first=True)
            s.drop = nn.Dropout(0.3); s.fc = nn.Linear(hid, 1)
        def forward(s, x):
            o, _ = s.lstm(x); return s.fc(s.drop(o[:, -1, :])).squeeze(-1)

    dev = "cpu"
    net = Net(X.shape[2]).to(dev)
    pw = torch.tensor([(ytr == 0).sum() / max((ytr == 1).sum(), 1)], dtype=torch.float32)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pw)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
    Xtr_t = torch.tensor(Xtr); ytr_t = torch.tensor(ytr)
    Xval_t = torch.tensor(Xval); Xte_t = torch.tensor(Xte)

    best_val = 1e9; best_state = None; patience = 0
    for ep in range(EPOCHS):
        net.train()
        perm = torch.randperm(len(Xtr_t))
        for b in range(0, len(perm), BATCH):
            idx = perm[b:b + BATCH]
            opt.zero_grad()
            loss = lossf(net(Xtr_t[idx]), ytr_t[idx]); loss.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            vl = lossf(net(Xval_t), torch.tensor(yval)).item()
        if vl < best_val - 1e-4:
            best_val = vl; best_state = {k: v.clone() for k, v in net.state_dict().items()}; patience = 0
        else:
            patience += 1
            if patience >= 5: break
    if best_state: net.load_state_dict(best_state)

    net.eval()
    with torch.no_grad():
        p = torch.sigmoid(net(Xte_t)).numpy()
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(yte, p) if len(np.unique(yte)) > 1 else float("nan")

    rule_idx = np.where((meta_te.should_scalp.values == 1) & (meta_te.align4h.values == 1))[0]
    k = len(rule_idx)
    model_idx = np.argsort(-p)[:k] if 0 < k <= len(p) else np.arange(len(p))

    days = max((meta_te.ts.max() - meta_te.ts.min()).days, 1)
    def report(tag, idx):
        _, wr, ex = expect(yte[idx])
        dk = dedupe_mask(meta_te, idx)
        _, dwr, dex = expect(yte[dk])
        print(f"  {tag:22} raw n={len(idx):>5} WR={wr*100:>4.1f}% exp={ex:>+5.2f}R | "
              f"dedup n={len(dk):>4} ({len(dk)/days:>4.1f}/d) WR={dwr*100:>4.1f}% exp={dex:>+5.2f}R")
        return dex

    print(f"\n### {symbol}  (train {vcut}, test {len(Xte)}, {days}d unseen, LSTM AUC {auc:.3f})")
    report("take-ALL", np.arange(len(p)))
    r_dex = report("RULE (pass+WITH-4H)", rule_idx)
    m_dex = report("LSTM (=rule freq)", model_idx)
    v = ("LSTM BEATS rule" if m_dex > r_dex + 0.03 else
         "LSTM ~= rule (no gain)" if abs(m_dex - r_dex) <= 0.03 else "LSTM WORSE than rule")
    print(f"  -> VERDICT: {v}  (LSTM {m_dex:+.2f}R vs rule {r_dex:+.2f}R, deduped)")
    return (symbol, r_dex, m_dex, v)


if __name__ == "__main__":
    print(f"LSTM seq={SEQ_LEN} | RR {RR} | walk-forward 75/25 | must beat rule to deploy")
    out = []
    for sym in ["BTC", "ETH", "XAUUSD", "XAGUSD"]:
        try:
            r = run(sym)
            if r: out.append(r)
        except Exception as e:
            print(f"### {sym}: ERROR {e}")
    print("\n" + "=" * 58 + "\nSUMMARY (deduped, out-of-sample)")
    for sym, rd, md, v in out:
        print(f"  {sym:8} rule {rd:+.2f}R -> LSTM {md:+.2f}R   {v}")
