"""Follow-up tests on smc_entry_rows.json. Read-only.
 A. how often a 'liquidity grab' is present at all (detector on rolling history)
 B. HTF consensus x direction
 C. candidate filters on the real outcomes, per half
 D. 'wait for the candle to close': enter at the close of the signal candle
    only if it closed for the trade, same stop -> first-touch 1R/1R + own target"""
import json
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from features.smc.liquidity_grab import detect_liquidity_grab

UTC = timezone.utc
R = json.load(open(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\SESSION_HANDOFF\scripts\smc_entry_rows.json"))
R.sort(key=lambda r: r["t_in"])
MID = R[len(R) // 2]["t_in"]
mt5.initialize(timeout=10000)
TF = {"5m": (mt5.TIMEFRAME_M5, 300), "15m": (mt5.TIMEFRAME_M15, 900), "30m": (mt5.TIMEFRAME_M30, 1800)}

print("=== A. HOW RARE IS A 'LIQUIDITY GRAB'? (production detector, lookback 20, on every closed bar) ===")
for sym in ("BTCUSDm", "ETHUSDm"):
    for tf in ("5m", "15m"):
        r = mt5.copy_rates_from_pos(sym, TF[tf][0], 1, 3000)
        df = pd.DataFrame(r)
        hits = sum(detect_liquidity_grab(df.iloc[k - 100:k], lookback=20).get("detected", False)
                   for k in range(100, len(df)))
        print(f"  {sym[:-1]} {tf}: a grab is 'detected' on {100 * hits / (len(df) - 100):.0f}% of bars")


def p11(rows):
    v = [r["grid"]["1.0/1.0"] for r in rows]
    return 100 * np.mean([x > 0 for x in v]) if v else float("nan")


def line(lab, rows):
    h1 = [r for r in rows if r["t_in"] < MID]
    h2 = [r for r in rows if r["t_in"] >= MID]
    sr = lambda x: sum(r["r"] for r in x if r["r"] is not None)
    print(f"  {lab:46s} n={len(rows):3d}  +1R-first {p11(rows):4.1f}% (halves {p11(h1):4.1f} / {p11(h2):4.1f})  "
          f"actual {sr(rows):+6.1f}R (halves {sr(h1):+5.1f} / {sr(h2):+5.1f})  ${sum(r['pnl'] for r in rows):+8.2f}")


print("\n=== B. HTF CONSENSUS x DIRECTION ===")
for cons in ("bullish", "bearish", "conflict", "neutral"):
    for dr in ("buy", "sell"):
        sel = [r for r in R if r["sig"] and r["sig"].get("htf_consensus") == cons and r["dir"] == dr]
        if sel:
            line(f"{cons} consensus, {dr}", sel)
al = [r for r in R if r["sig"] and ((r["sig"].get("htf_consensus") == "bullish") == (r["dir"] == "buy"))
      and r["sig"].get("htf_consensus") in ("bullish", "bearish")]
line("ALIGNED (consensus = trade direction)", al)
line("CONFLICT", [r for r in R if r["sig"] and r["sig"].get("htf_consensus") == "conflict"])

print("\n=== C. CANDIDATE FILTERS ON THE REAL TRADES (everything = baseline) ===")
c = lambda r, k: r["ctx"].get(k)
filters = [
    ("everything (as traded)", lambda r: True),
    ("block HTF 'conflict'", lambda r: not (r["sig"] and r["sig"].get("htf_consensus") == "conflict")),
    ("only with the last-1h move (>0)", lambda r: (c(r, "mom1h") or 0) > 0),
    ("only with the last-1h move (>= 0.5 ATR)", lambda r: (c(r, "mom1h") or 0) >= 0.5),
    ("skip if against the 4h move by > 2 ATR", lambda r: (c(r, "mom4h") or 0) > -2),
    ("skip if on the wrong side of EMA50 15m by > 1 ATR", lambda r: (c(r, "ema50_15m") or 0) > -1),
    ("5m signals only", lambda r: r["tf"] == "5m"),
    ("no 15m signals", lambda r: r["tf"] != "15m"),
    ("block conflict + with the 1h move", lambda r: not (r["sig"] and r["sig"].get("htf_consensus") == "conflict")
     and (c(r, "mom1h") or 0) > 0),
    ("BTC + ETH only", lambda r: r["sym"] in ("BTCUSD", "ETHUSD")),
    ("BTC + ETH, block conflict", lambda r: r["sym"] in ("BTCUSD", "ETHUSD")
     and not (r["sig"] and r["sig"].get("htf_consensus") == "conflict")),
]
for lab, f in filters:
    line(lab, [r for r in R if f(r)])

print("\n=== D. WAIT FOR THE SIGNAL CANDLE TO CLOSE (enter at its close only if it closed for the trade) ===")


def first_touch(sym, t0, entry, buy, rp, T, point):
    m = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, datetime.fromtimestamp(t0, UTC),
                             datetime.fromtimestamp(t0 + 86400, UTC))
    if m is None or not len(m) or rp <= 0:
        return None
    sp = m["spread"] * point
    fav = ((m["high"] - entry) if buy else (entry - (m["low"] + sp))) / rp
    adv = ((m["low"] - entry) if buy else (entry - (m["high"] + sp))) / rp
    hs, ht = np.nonzero(adv <= -1)[0], np.nonzero(fav >= T)[0]
    i_s = hs[0] if len(hs) else 10 ** 9
    i_t = ht[0] if len(ht) else 10 ** 9
    if i_t < i_s:
        return T
    if i_s < 10 ** 9:
        return -1.0
    cl = m["close"][-1]
    return float(((cl - entry) if buy else (entry - cl - sp[-1])) / rp)


res = {"orig11": [], "wait11": [], "origT": [], "waitT": [], "skipped": 0, "t": []}
for r in R:
    sym = r["sym"] + "m"
    tfc, sec = TF.get(r["tf"], TF["15m"])
    buy = r["dir"] == "buy"
    si = mt5.symbol_info(sym)
    c_open = r["t_sig"] - r["t_sig"] % sec
    m = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, datetime.fromtimestamp(r["t_sig"] - 60, UTC),
                             datetime.fromtimestamp(c_open + sec - 60, UTC))
    if m is None or len(m) < 1:
        continue
    px_sig, cl = float(m["close"][0]), float(m["close"][-1])
    if (cl > px_sig) != buy:
        res["skipped"] += 1
        continue                                    # candle closed against: no trade
    t_e = c_open + sec
    spe = float(m["spread"][-1]) * si.point
    e = cl + spe if buy else cl
    rp_new = (e - r["sl0"]) if buy else (r["sl0"] - e)
    if rp_new <= 0:
        res["skipped"] += 1
        continue
    rp_old = (r["entry"] - r["sl0"]) if buy else (r["sl0"] - r["entry"])
    tp_dist = abs(r["tp0"] - r["entry"]) if r["tp0"] else 3 * rp_old
    T_new = max(0.5, ((r["tp0"] - e) if buy else (e - r["tp0"])) / rp_new) if r["tp0"] else 3.0
    w11 = first_touch(sym, t_e, e, buy, rp_new, 1.0, si.point)
    wT = first_touch(sym, t_e, e, buy, rp_new, T_new, si.point)
    if w11 is None or wT is None:
        continue
    res["orig11"].append(r["grid"]["1.0/1.0"])
    res["wait11"].append(w11)
    res["origT"].append(first_touch(sym, r["t_in"], r["entry"], buy, rp_old, r["tp_r"] or 3.0, si.point))
    res["waitT"].append(wT)
    res["t"].append(r["t_in"])
mt5.shutdown()
n = len(res["wait11"])
t = np.array(res["t"])
for lab, k in (("original entries (same trades)", "orig11"), ("wait for close", "wait11")):
    v = np.array(res[k])
    print(f"  {lab:32s} 1R/1R: +1R-first {100 * np.mean(v > 0):4.1f}%  mean {v.mean():+.3f}R  "
          f"(halves {v[t < MID].mean():+.3f} / {v[t >= MID].mean():+.3f})")
for lab, k in (("original entries, own target", "origT"), ("wait for close, own target", "waitT")):
    v = np.array([x for x in res[k] if x is not None])
    print(f"  {lab:32s} mean {v.mean():+.3f}R per trade, total {v.sum():+.1f}R")
print(f"  {res['skipped']} of {len(R)} signals skipped because the candle closed against the trade "
      f"(those trades made {sum(r['r'] for r in R if r['r'] is not None) - 0:+.1f}R overall; see section 5 of the main study)")
