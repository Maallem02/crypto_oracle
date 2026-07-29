"""Full diagnostic of all trades since Monday 2026-07-20. Looks for fixable
patterns: losing symbols, losing hours/sessions, direction bias, oversized
trades, gate leaks, and whether the recent changes are behaving.
Expectancy in R (normalizes across the real->demo account switch)."""
import sqlite3, sys, json, warnings
from collections import defaultdict
from datetime import datetime
import numpy as np
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
SINCE = "2026-07-20"
SYMBOL_MAP = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm",
              "EURUSD": "EURUSDm", "USDJPY": "USDJPYm", "GBPJPY": "GBPJPYm",
              "SOL": "SOLUSDm", "BNB": "BNBUSDm", "XRP": "XRPUSDm"}
RR_TP = 2.5

import MetaTrader5 as mt5
mt5.initialize(timeout=10000)

def risk_usd(sym, action, vol, entry, sl):
    try:
        m = SYMBOL_MAP.get((sym or "").upper())
        if m and entry and sl and vol:
            ot = mt5.ORDER_TYPE_BUY if action == "buy" else mt5.ORDER_TYPE_SELL
            p = mt5.order_calc_profit(ot, m, float(vol), float(entry), float(sl))
            if p and abs(p) > 1e-9:
                return abs(p)
    except Exception:
        pass
    return None

conn = sqlite3.connect(DB)
rows = conn.execute("SELECT timestamp,symbol,action,data,outcome,profit,closed_at FROM scalp_log "
                    "WHERE ticket IS NOT NULL AND outcome IS NOT NULL AND timestamp>=? ORDER BY id",
                    (SINCE,)).fetchall()
conn.close()

T = []
for ts, sym, act, data, outcome, profit, closed in rows:
    d = json.loads(data); res = d.get("result") or {}
    entry = res.get("price") or d.get("entry"); sl = res.get("sl") or d.get("sl")
    vol = res.get("volume")
    rk = risk_usd(sym, act, vol, entry, sl)
    r = (profit / rk) if (rk and profit is not None) else None
    conds = " ".join(str(c) for c in (d.get("conditions") or []))
    T.append(dict(ts=ts, sym=sym, act=act, score=d.get("score"), ml=d.get("ml_win_prob"),
                  htf=d.get("htf_consensus"), vol=vol, profit=profit or 0, outcome=outcome,
                  r=r, rk=rk, conds=conds, hour=datetime.fromisoformat(ts).hour - 1))
mt5.shutdown()

def stat(items):
    n = len(items); w = sum(1 for x in items if x["outcome"] == 1)
    be = sum(1 for x in items if abs(x["profit"]) < 0.15)
    pnl = sum(x["profit"] for x in items)
    rs = [x["r"] for x in items if x["r"] is not None]
    exp = np.mean(rs) if rs else float("nan")
    return n, w, be, pnl, exp

print(f"=== ALL TRADES SINCE {SINCE} (Monday) ===  [{len(T)} closed trades]")
n, w, be, pnl, exp = stat(T)
print(f"record: {w}W / {n-w-be}L / {be}BE = {100*w/n:.0f}% WR | net P&L ${pnl:+.2f} | expectancy {exp:+.3f}R")
print(f"avg win {np.mean([x['r'] for x in T if x['r'] and x['r']>0] or [0]):+.2f}R | "
      f"avg loss {np.mean([x['r'] for x in T if x['r'] and x['r']<0] or [0]):+.2f}R")

print("\n=== BY SYMBOL ===")
bs = defaultdict(list)
for x in T: bs[x["sym"]].append(x)
for sym in sorted(bs, key=lambda s: sum(y["profit"] for y in bs[s])):
    n, w, be, pnl, exp = stat(bs[sym])
    flag = "  <-- LOSING, review" if exp < -0.1 and n >= 4 else ""
    print(f"  {sym:7} n={n:>2} WR={100*w/n:>3.0f}% exp={exp:+.2f}R  P&L ${pnl:+6.2f}{flag}")

print("\n=== BY DIRECTION ===")
for side in ["buy", "sell"]:
    sub = [x for x in T if x["act"] == side]
    if sub:
        n, w, be, pnl, exp = stat(sub)
        print(f"  {side:5} n={n:>2} WR={100*w/n:>3.0f}% exp={exp:+.2f}R  P&L ${pnl:+.2f}")

print("\n=== BY SESSION (server hour) ===")
sess = {"02-08 Asia": range(2,8), "08-13 London": range(8,13), "13-17 NYover": range(13,17),
        "17-21 lateNY": range(17,21), "21-02 deadzone": list(range(21,24))+list(range(0,2))}
for name, hrs in sess.items():
    sub = [x for x in T if x["hour"] % 24 in hrs]
    if sub:
        n, w, be, pnl, exp = stat(sub)
        flag = "  <-- should be empty (dead zone)!" if "dead" in name and n>0 else ""
        print(f"  {name:15} n={n:>2} WR={100*w/n:>3.0f}% exp={exp:+.2f}R  P&L ${pnl:+.2f}{flag}")

print("\n=== BIGGEST LOSSES (top 6) — are they oversized / counter-trend? ===")
for x in sorted(T, key=lambda z: z["profit"])[:6]:
    ct = "COUNTER-TREND!" if "COUNTER-TREND" in x["conds"] or "waived" in x["conds"] else ""
    print(f"  {x['ts'][5:16]} {x['sym']:6} {x['act']:4} vol={x['vol']} score={x['score']} "
          f"${x['profit']:+.2f} ({x['r']:+.2f}R if measured) {ct}")

print("\n=== POST-RESTART trades (today's new-config trades) ===")
post = [x for x in T if x["ts"] >= "2026-07-23"]
print(f"  {len(post)} trades since 07-23:")
for x in post:
    print(f"    {x['ts'][11:16]} {x['sym']:6} {x['act']:4} vol={x['vol']} ${x['profit']:+.2f} outcome={x['outcome']}")
