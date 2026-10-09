"""Analysis of vp_entry_rows.json (vp_entry_study.py). Read-only."""
import json
from datetime import datetime, timezone

import numpy as np

UTC = timezone.utc
R = json.load(open(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\SESSION_HANDOFF\scripts\vp_entry_rows.json"))
R.sort(key=lambda r: r["t"])
LIVE = {"BTCUSD", "ETHUSD", "XAUUSD", "USDJPY"}
MID = R[len(R) // 2]["t"]
d = lambda ts: datetime.fromtimestamp(ts, UTC).strftime("%d/%m")


def st(rows, key="r2"):
    if not rows:
        return "n=  0"
    r = np.array([x[key] for x in rows])
    gp, gl = r[r > 0].sum(), -r[r <= 0].sum()
    h1 = np.mean([x[key] for x in rows if x["t"] < MID] or [np.nan])
    h2 = np.mean([x[key] for x in rows if x["t"] >= MID] or [np.nan])
    return (f"n={len(r):3d} WR {100 * (r > 0).mean():3.0f}% avg {r.mean():+.3f}R sum {r.sum():+6.1f}R "
            f"PF {gp / gl if gl else 9.9:4.2f} | halves avg {h1:+.2f} / {h2:+.2f}")


def take(rows):
    """live rule: 1 position per symbol, next signal only after the previous one closed"""
    busy, out = {}, []
    for r in rows:
        if busy.get(r["sym"], 0) > r["t"]:
            continue
        busy[r["sym"]] = r["x2"]
        out.append(r)
    return out


print(f"{len(R)} re-entry signals {d(R[0]['t'])} -> {d(R[-1]['t'])}, halves split {d(MID)}")
print("\n=== 0. RESULT AT THE LIVE RULE (2R) ===")
print(f"  every signal              {st(R)}")
print(f"  live symbols, every signal {st([r for r in R if r['sym'] in LIVE])}")
L = take([r for r in R if r["sym"] in LIVE])
print(f"  live symbols, 1/symbol     {st(L)}")
Lr = take([r for r in R if r["sym"] in LIVE and r["room"] >= 1.0])
print(f"  + room filter (AS LIVE)    {st(Lr)}")
for s in sorted({r["sym"] for r in R}):
    print(f"    {s:7s} every signal {st([r for r in R if r['sym'] == s])}")

print("\n=== 1. IS THERE A DIRECTION EDGE? (+1R before -1R; coin flip minus spread ~ 47%) ===")
for lab, rows in (("all", R), ("1st half", [r for r in R if r["t"] < MID]), ("2nd half", [r for r in R if r["t"] >= MID])):
    b = [x for r in rows for x in r["base"]]
    mi = [r["mirror"] for r in rows if r["mirror"] is not None]
    print(f"  {lab:9s} VP entries {100 * np.mean([r['r1'] > 0 for r in rows]):4.1f}% (n={len(rows)})   "
          f"same moment OPPOSITE {100 * np.mean([x > 0 for x in mi]):4.1f}%   "
          f"random times same direction {100 * np.mean([x > 0 for x in b]):4.1f}% (n={len(b)})")
for s in sorted({r["sym"] for r in R}):
    rows = [r for r in R if r["sym"] == s]
    b = [x for r in rows for x in r["base"]]
    print(f"    {s:7s} VP {100 * np.mean([r['r1'] > 0 for r in rows]):4.1f}%   random {100 * np.mean([x > 0 for x in b]):4.1f}%   n={len(rows)}")


def rep(name, get, cuts=None, cats=False, rows=None):
    rows = rows if rows is not None else R
    vals = [(get(r), r) for r in rows]
    vals = [(v, r) for v, r in vals if v is not None]
    if not vals:
        return
    if cats:
        lab_of = str
        groups = sorted({str(v) for v, _ in vals})
    else:
        q = cuts
        labs = [f"< {q[0]:g}"] + [f"{q[k]:g}-{q[k + 1]:g}" for k in range(len(q) - 1)] + [f">= {q[-1]:g}"]
        lab_of = lambda v: labs[int(np.searchsorted(q, v, side="right"))]
        groups = labs
    allm = np.mean([r["r2"] for r in rows])
    print(f"\n  {name}   (all: avg {allm:+.3f}R at 2R)")
    for g in groups:
        sel = [r for v, r in vals if lab_of(v) == g]
        if len(sel) < 10:
            continue
        a1 = [r["r2"] for r in sel if r["t"] < MID]
        a2 = [r["r2"] for r in sel if r["t"] >= MID]
        flag = ""
        if len(a1) >= 8 and len(a2) >= 8:
            if np.mean(a1) >= allm + 0.15 and np.mean(a2) >= allm + 0.15:
                flag = "  <- BETTER in both halves"
            elif np.mean(a1) <= allm - 0.15 and np.mean(a2) <= allm - 0.15:
                flag = "  <- WORSE in both halves"
        print(f"    {g:24s} {st(sel)}  +1R-first {100 * np.mean([r['r1'] > 0 for r in sel]):3.0f}%{flag}")


print("\n=== 2. WHAT SEPARATES GOOD RE-ENTRIES FROM BAD ONES (every signal, live symbols + XAG) ===")
c = lambda k: (lambda r: r["ctx"].get(k))
rep("which edge", lambda r: "buy at VAL" if r["dir"] == "buy" else "sell at VAH", cats=True)
rep("n-th signal on this edge in the session", lambda r: min(r["n_edge"], 3), cats=True)
rep("bars closed outside before coming back", lambda r: min(r["n_out"], 4), cats=True)
rep("depth of the excursion beyond the edge (x ATR)", lambda r: r["depth"], cuts=[0.5, 1.0, 2.0])
rep("stop size (x ATR)", lambda r: r["risk_atr"], cuts=[0.75, 1.25, 2.0])
rep("value-area width (x ATR)", lambda r: r["va_w"], cuts=[3, 6, 10])
rep("room to the far edge (R)", lambda r: r["room"], cuts=[1, 2, 4])
rep("distance to the POC (R)", lambda r: r["to_poc"], cuts=[0.5, 1, 2])
rep("how far back inside the trigger closed (x ATR)", lambda r: r["back_in"], cuts=[0.25, 0.5, 1.0])
rep("H1 EMA50 slope vs trade", c("h1_slope"), cats=True)
rep("price vs H1 EMA50", c("h1_side"), cats=True)
rep("H4 EMA50 slope vs trade", c("h4_slope"), cats=True)
rep("previous day's candle vs trade", c("prevday"), cats=True)
rep("move over the last 24 h, signed with the trade (x ATR15)", c("mom24"), cuts=[-8, -3, 0, 3])
rep("H1 ADX", c("adx_h1"), cuts=[20, 25, 35])
rep("where the session opened", c("open_vs_va"), cats=True)
rep("hour UTC", lambda r: (r["hour"] // 6) * 6, cats=True)
rep("weekday (0 = Mon)", lambda r: r["wd"], cats=True)
rep("spread as share of 1R", lambda r: r["spread_r"], cuts=[0.03, 0.06, 0.1])

print("\n=== 3. SAME EDGE AGAIN AFTER A STOP? (2nd+ signal on an edge, by what the previous one did) ===")
prev_res = {}
seq = {"previous on this edge was STOPPED": [], "previous on this edge WON": [], "previous still open": []}
for r in R:
    key = (r["sym"], r["dir"], r["t"] // 86400)
    p = prev_res.get(key)
    if p is not None:
        if p["x2"] > r["t"]:
            seq["previous still open"].append(r)
        elif p["r2"] < 0:
            seq["previous on this edge was STOPPED"].append(r)
        else:
            seq["previous on this edge WON"].append(r)
    prev_res[key] = r
for k, v in seq.items():
    print(f"  {k:36s} {st(v)}")

print("\n=== 4. CANDIDATE FILTERS ON THE LIVE-LIKE SET (live symbols, room >= 1R, 1 position per symbol, 2R) ===")
base = [r for r in R if r["sym"] in LIVE and r["room"] >= 1.0]
FILTERS = [
    ("as live now", lambda r: True),
    ("first signal per edge per session only", lambda r: r["n_edge"] == 1),
    ("no new trade on an edge after a stop there today",
     lambda r: not any(q["sym"] == r["sym"] and q["dir"] == r["dir"] and q["t"] // 86400 == r["t"] // 86400
                       and q["t"] < r["t"] and q["x2"] <= r["t"] and q["r2"] < 0 for q in R)),
    ("H1 slope with the trade", lambda r: r["ctx"].get("h1_slope") == "with"),
    ("H1 slope against the trade", lambda r: r["ctx"].get("h1_slope") == "against"),
    ("H4 slope with the trade", lambda r: r["ctx"].get("h4_slope") == "with"),
    ("skip if 24h move against > 8 ATR", lambda r: (r["ctx"].get("mom24") or 0) > -8),
    ("H1 ADX < 25", lambda r: (r["ctx"].get("adx_h1") or 0) < 25),
    ("H1 ADX < 35", lambda r: (r["ctx"].get("adx_h1") or 0) < 35),
]
for lab, f in FILTERS:
    sel = take([r for r in base if f(r)])
    print(f"  {lab:48s} {st(sel)}")
