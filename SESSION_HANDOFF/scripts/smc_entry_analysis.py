"""Analysis of smc_entry_rows.json (built by smc_entry_study.py). Read-only."""
import json
from datetime import datetime, timezone

import numpy as np

UTC = timezone.utc
R = json.load(open(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\SESSION_HANDOFF\scripts\smc_entry_rows.json"))
R.sort(key=lambda r: r["t_in"])
MID = R[len(R) // 2]["t_in"]
H1 = [r for r in R if r["t_in"] < MID]
H2 = [r for r in R if r["t_in"] >= MID]


def d(ts):
    return datetime.fromtimestamp(ts, UTC).strftime("%d/%m")


def p11(rows, key="grid"):
    v = [r[key]["1.0/1.0"] for r in rows if r.get(key)]
    return (100 * np.mean([x > 0 for x in v]) if v else float("nan")), (np.mean(v) if v else float("nan")), len(v)


def se(v):
    return np.std(v, ddof=1) / np.sqrt(len(v)) if len(v) > 1 else float("nan")


print(f"{len(R)} SMC trades {d(R[0]['t_in'])} -> {d(R[-1]['t_in'])}   halves split {d(MID)}")
pn = np.array([r["pnl"] for r in R])
rr = np.array([r["r"] for r in R if r["r"] is not None])
print(f"actual: WR {100 * np.mean(pn > 0):.0f}%  net {pn.sum():+.2f}$  PF {pn[pn > 0].sum() / -pn[pn <= 0].sum():.2f}"
      f"  sum {rr.sum():+.1f}R  avg {rr.mean():+.3f}R")

# ── 1. is there ANY direction edge? ─────────────────────────────────────────
print("\n=== 1. DIRECTION EDGE: does price go +1R before -1R? (50% = coin flip, spread makes it ~47-48%) ===")
for lab, rows in (("all", R), ("1st half", H1), ("2nd half", H2)):
    w, m, n = p11(rows)
    wm, mm, _ = p11(rows, "mirror")
    b = [x for r in rows for x in r["base"]]
    print(f"  {lab:9s} bot entries {w:4.1f}% (n={n})   same moment OPPOSITE direction {wm:4.1f}%   "
          f"random times same direction {100 * np.mean([x > 0 for x in b]):4.1f}% (n={len(b)})")
print("\n  price move after the entry, in R, in the trade's direction (mean +- 1 s.e.):")
for lab in ("15m", "1h", "4h", "24h"):
    v = [r["mv"][lab] for r in R if lab in r["mv"]]
    v1 = [r["mv"][lab] for r in H1 if lab in r["mv"]]
    v2 = [r["mv"][lab] for r in H2 if lab in r["mv"]]
    print(f"    after {lab:4s} {np.mean(v):+.3f} +- {se(v):.3f}R  (n={len(v)})   halves {np.mean(v1):+.3f} / {np.mean(v2):+.3f}")

# ── 2. stop / target grid ───────────────────────────────────────────────────
print("\n=== 2. SAME ENTRIES, OTHER STOP / TARGET (mean R per trade; stop and target in the trade's own R) ===")
keys = list(R[0]["grid"].keys())
Ts = sorted({float(k.split("/")[0]) for k in keys})
Ss = sorted({float(k.split("/")[1]) for k in keys})
print("            " + "".join(f"target {t:<5}" for t in Ts))
for s in Ss:
    cells = []
    for t in Ts:
        v = [r["grid"][f"{t}/{s}"] / s for r in R]           # per unit of risk actually taken
        cells.append(f"{np.mean(v):+.3f}     ")
    print(f"  stop {s:3.1f}R  " + " ".join(cells))
print("  (each cell: average result divided by the stop size = R per unit of risk; >0 = profitable)")

# ── 3. stopped but right? ───────────────────────────────────────────────────
st = [r for r in R if r["grid"]["1.0/1.0"] < 0]
print(f"\n=== 3. TRADES THAT HIT -1R BEFORE +1R: {len(st)} ===")
for s in (1.5, 2.0, 3.0):
    ok = sum(1 for r in st if r["grid"][f"1.0/{s}"] > 0)
    print(f"  with a stop of {s}R instead, {ok}/{len(st)} ({100 * ok / len(st):.0f}%) would have reached +1R first")
mae_w = [r["mae"] for r in R if r["grid"]["1.0/1.0"] > 0]
print(f"  winners' deepest dip before +1R... (whole trade MAE) median {np.median(mae_w):+.2f}R")
fast = [r for r in R if r["pnl"] <= 0 and (r["t_out"] - r["t_in"]) <= 3600]
print(f"  actual losers stopped within 1 h of the fill: {len(fast)}/{sum(1 for r in R if r['pnl'] <= 0)}")
print(f"  actual losers that never reached +0.5R: {sum(1 for r in R if r['pnl'] <= 0 and r['mfe'] < .5)}"
      f"/{sum(1 for r in R if r['pnl'] <= 0)}")


# ── 4. what separates good from bad entries? (in BOTH halves) ───────────────
def bucket_report(name, get, cuts=None, cats=None):
    vals = [(get(r), r) for r in R]
    vals = [(v, r) for v, r in vals if v is not None]
    if len(vals) < 40:
        return
    if cats is None:
        x = np.array([v for v, _ in vals], dtype=float)
        q = cuts or list(np.quantile(x, [1 / 3, 2 / 3]))
        labs = [f"< {q[0]:.2f}"] + [f"{q[k]:.2f}-{q[k + 1]:.2f}" for k in range(len(q) - 1)] + [f">= {q[-1]:.2f}"]

        def lab_of(v):
            k = int(np.searchsorted(q, v, side="right"))
            return labs[k]
        groups = labs
    else:
        def lab_of(v):
            return str(v)
        groups = sorted({str(v) for v, _ in vals})
    out = []
    for g in groups:
        sel = [r for v, r in vals if lab_of(v) == g]
        if len(sel) < 8:
            continue
        a = p11(sel)
        h1 = p11([r for r in sel if r["t_in"] < MID])
        h2 = p11([r for r in sel if r["t_in"] >= MID])
        act = sum(r["r"] for r in sel if r["r"] is not None)
        out.append((g, a, h1, h2, act, len(sel)))
    if len(out) < 2:
        return
    allw = p11(R)[0]
    print(f"\n  {name}")
    for g, a, h1, h2, act, n in out:
        flag = ""
        if h1[2] >= 8 and h2[2] >= 8:
            if h1[0] >= allw + 6 and h2[0] >= allw + 6:
                flag = "  <- BETTER in both halves"
            elif h1[0] <= allw - 6 and h2[0] <= allw - 6:
                flag = "  <- WORSE in both halves"
        print(f"    {g:18s} n={n:3d}  +1R-first {a[0]:4.1f}%  (halves {h1[0]:4.1f}% / {h2[0]:4.1f}%)  "
              f"actual {act:+6.1f}R{flag}")


print(f"\n=== 4. WHAT SEPARATES GOOD ENTRIES FROM BAD ONES (+1R-first rate; overall {p11(R)[0]:.1f}%) ===")
c = lambda k: (lambda r: r["ctx"].get(k))
s = lambda k: (lambda r: r["sig"].get(k) if r["sig"] else None)
bucket_report("symbol", lambda r: r["sym"], cats=True)
bucket_report("direction", lambda r: r["dir"], cats=True)
bucket_report("entry type", lambda r: "limit" if r["limit"] else "market", cats=True)
bucket_report("signal timeframe", lambda r: r["tf"], cats=True)
bucket_report("move in the trade's direction over the previous 1 h (x ATR15)", c("mom1h"), cuts=[-1.0, 0.0, 1.0])
bucket_report("move in the trade's direction over the previous 4 h (x ATR15)", c("mom4h"), cuts=[-2.0, 0.0, 2.0])
bucket_report("stretch from EMA50 15m, signed (x ATR15)", c("ema50_15m"), cuts=[-1.0, 0.0, 1.5, 3.0])
bucket_report("place in the last 24 h range (0 = best price for the trade, 1 = worst)", c("range24"), cuts=[0.33, 0.66])
bucket_report("H1 EMA50 slope vs trade", c("h1_trend"), cats=True)
bucket_report("H4 EMA50 slope vs trade", c("h4_trend"), cats=True)
bucket_report("price vs H4 EMA50", c("h4_side"), cats=True)
bucket_report("stop size (x ATR15)", c("stop_atr"), cuts=[1.5, 2.5, 3.5])
bucket_report("spread as share of 1R", c("spread_R"), cuts=[0.03, 0.06, 0.10])
bucket_report("how far into its candle the signal fired (0 = just opened, 1 = about to close)", c("into_candle"), cuts=[0.33, 0.66])
bucket_report("bot score", s("scalping_score"), cuts=[75, 85])
bucket_report("ADX", s("adx"), cuts=[20, 30])
bucket_report("RSI", s("rsi"), cuts=[40, 50, 60])
bucket_report("ATR ratio", s("atr_ratio"), cuts=[0.8, 1.1])
bucket_report("P/D position %", s("pd_pct"), cuts=[35, 50, 65])
bucket_report("HTF consensus", s("htf_consensus"), cats=True)
bucket_report("OB/FVG confluence", s("smc_confluence"), cats=True)
bucket_report("structure", s("structure"), cats=True)
bucket_report("LG strength", s("lg_strength"), cuts=[0.02, 0.05])
bucket_report("volume ratio", s("volume_ratio"), cuts=[0.8, 1.2])
bucket_report("hour UTC", lambda r: (datetime.fromtimestamp(r["t_in"], UTC).hour // 6) * 6, cats=True)

# ── 5. the unfinished candle ────────────────────────────────────────────────
print("\n=== 5. THE SIGNAL CANDLE: the bot decides on a candle that is still forming ===")
cc = [r for r in R if r["ctx"].get("candle_close_R") is not None]
fav_ = [r for r in cc if r["ctx"]["candle_close_R"] > 0]
adv_ = [r for r in cc if r["ctx"]["candle_close_R"] <= 0]
print(f"  candle then closed FOR the trade:     n={len(fav_):3d}  +1R-first {p11(fav_)[0]:.1f}%  "
      f"actual {sum(r['r'] for r in fav_ if r['r'] is not None):+.1f}R")
print(f"  candle then closed AGAINST the trade: n={len(adv_):3d}  +1R-first {p11(adv_)[0]:.1f}%  "
      f"actual {sum(r['r'] for r in adv_ if r['r'] is not None):+.1f}R")
print(f"  signals fired in the first third of the candle: "
      f"{sum(1 for r in R if r['ctx'].get('into_candle', 1) < 1 / 3)}/{len(R)}")

# ── 6. limits ───────────────────────────────────────────────────────────────
lim = [r for r in R if r["limit"]]
if lim:
    dl = [r["ctx"]["fill_delay_min"] for r in lim]
    print(f"\n=== 6. LIMIT ORDERS: {len(lim)} filled, delay signal->fill median {np.median(dl):.0f} min "
          f"(>1 h: {sum(x > 60 for x in dl)}) ===")
    for lab, sel in (("filled < 15 min", [r for r in lim if r["ctx"]["fill_delay_min"] < 15]),
                     ("filled 15-120 min", [r for r in lim if 15 <= r["ctx"]["fill_delay_min"] < 120]),
                     ("filled > 2 h", [r for r in lim if r["ctx"]["fill_delay_min"] >= 120])):
        if sel:
            print(f"  {lab:18s} n={len(sel):3d}  +1R-first {p11(sel)[0]:.1f}%  "
                  f"actual {sum(r['r'] for r in sel if r['r'] is not None):+.1f}R")

# ── 7. the current exit on these entries, for reference ─────────────────────
print("\n=== 7. TARGETS ===")
tp = [r["tp_r"] for r in R if r["tp_r"]]
print(f"  target distance median {np.median(tp):.2f}R; trades whose path reached it within 24 h: "
      f"{sum(1 for r in R if r['tp_r'] and r['mfe'] >= r['tp_r'])}/{len(R)}")
for t in (1.0, 1.5, 2.0, 3.0):
    print(f"  reached +{t}R (whole life of the trade): {100 * np.mean([r['mfe'] >= t for r in R]):.0f}%")
