"""This week's closed trades (Mon 28/09 00:00 local -> now), demo 262890622. Read-only.
Per trade: bot, setup, exit, $ and R, best point before closing (MFE), news
around it, the market's direction that day. Then per bot, per day, today's
losers explained, and VP management replayed on its real trades."""
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import MetaTrader5 as mt5

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from features.news.calendar import COUNTRY_CURRENCY, _fetch, _get_currencies, _parse_dt

UTC = timezone.utc
BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
BOT = {234000: "SMC", 234100: "VP", 0: "manual"}
now_l = datetime.now().astimezone()
WEEK = now_l.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now_l.weekday())
TODAY = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
db = sqlite3.connect(f"{BK}\\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
mt5.initialize(timeout=10000)
acc = mt5.account_info()

# ── closed positions whose LAST exit is this week (entry may be earlier) ─────
deals = mt5.history_deals_get(WEEK.astimezone(UTC) - timedelta(days=7), datetime.now(UTC) + timedelta(hours=3)) or []
cash_week = sum(d.profit for d in deals if d.type not in (0, 1) and d.time >= WEEK.timestamp())
pos = defaultdict(list)
for d in deals:
    if d.type in (0, 1):
        pos[d.position_id].append(d)
T = []
for pid, ds in pos.items():
    full = sorted(mt5.history_deals_get(position=pid) or ds, key=lambda d: d.time)
    ins = [d for d in full if d.entry == 0]
    outs = [d for d in full if d.entry in (1, 3)]
    if not ins or not outs or sum(d.volume for d in outs) + 1e-9 < sum(d.volume for d in ins):
        continue
    last = outs[-1]
    if last.time < WEEK.timestamp():
        continue
    i = ins[0]
    buy = i.type == 0
    bot = BOT.get(i.magic, "other")
    # original stop / target and setup
    o = next(iter(mt5.history_orders_get(ticket=i.order) or []), None)
    sl0, tp0 = (o.sl, o.tp) if o is not None else (0, 0)
    setup = None
    r = db.execute("SELECT setup, sl, tp, data FROM vp_log WHERE ticket=? AND status='taken'", (pid,)).fetchone()
    if r:
        setup = {"va_reversal": "re-entry", "va_breakout": "breakout", "poc_bounce": "POC"}.get(r["setup"], r["setup"])
        sl0 = sl0 or r["sl"]
        tp0 = tp0 or r["tp"]
    if not sl0:
        r2 = db.execute("SELECT sl, tp1 FROM trade_signals WHERE ticket=? ORDER BY id DESC", (pid,)).fetchone()
        if r2:
            sl0, tp0 = r2[0] or 0, r2[1] or 0
    if bot == "SMC":
        setup = "limit entry" if (i.comment or "").startswith("CryptoOracle-P") else "market entry"
    risk = -mt5.order_calc_profit(0 if buy else 1, i.symbol, i.volume, i.price, sl0) if sl0 else None
    pnl = sum(d.profit + d.commission + d.swap for d in full)
    c = (last.comment or "").lower()
    kind = "target" if "[tp" in c else ("stop" if "[sl" in c else "closed by hand/other")
    if any("half" in (d.comment or "").lower() for d in outs):
        kind = "half@1R + " + ("rest at target" if "[tp" in c else "rest at entry stop" if "[sl" in c else "rest other")
    elif "[sl" in c and pnl > 0:
        kind = "trail/BE stop"
    # path: best / worst point before closing, in R
    mfe = mae = t_mfe = None
    if sl0:
        rp = (i.price - sl0) if buy else (sl0 - i.price)
        m1 = mt5.copy_rates_range(i.symbol, mt5.TIMEFRAME_M1,
                                  datetime.fromtimestamp(i.time - i.time % 60, UTC),
                                  datetime.fromtimestamp(last.time, UTC))
        if rp > 0 and m1 is not None and len(m1):
            si = mt5.symbol_info(i.symbol)
            sp = m1["spread"] * si.point
            fav = ((m1["high"] - i.price) if buy else (i.price - (m1["low"] + sp))) / rp
            adv = ((m1["low"] - i.price) if buy else (i.price - (m1["high"] + sp))) / rp
            k = int(np.argmax(fav))
            mfe, mae, t_mfe = float(fav.max()), float(adv.min()), (int(m1["time"][k]) - i.time) / 60
    T.append(dict(pid=pid, bot=bot, sym=i.symbol.rstrip("m"), dir="buy" if buy else "sell", setup=setup,
                  vol=i.volume, t_in=i.time, t_out=last.time, entry=i.price, sl0=sl0, tp0=tp0,
                  risk=risk, pnl=pnl, r=(pnl / risk if risk else None), exit=kind,
                  mfe=mfe, mae=mae, t_mfe=t_mfe, held=(last.time - i.time) / 60))
T.sort(key=lambda t: t["t_out"])


def lt(ts):
    return datetime.fromtimestamp(ts, UTC).astimezone()


def summ(rows, label):
    if not rows:
        print(f"  {label:28s} no trade")
        return
    p = np.array([t["pnl"] for t in rows])
    w, l = p[p > 0], p[p <= 0]
    rs = [t["r"] for t in rows if t["r"] is not None]
    print(f"  {label:28s} {len(p):3d} trades  {len(w)} won ({100 * len(w) / len(p):3.0f}%)  net {p.sum():+8.2f}  "
          f"avg win {w.mean() if len(w) else 0:+6.2f} / loss {l.mean() if len(l) else 0:+6.2f}  "
          f"PF {w.sum() / -l.sum() if l.sum() else float('inf'):4.2f}  avg R {np.mean(rs) if rs else 0:+.2f}")


print(f"WEEK {WEEK:%a %d/%m %H:%M} -> {now_l:%a %d/%m %H:%M} | balance now {acc.balance:.2f} equity {acc.equity:.2f} "
      f"| deposits this week {cash_week:+.2f}")
wk_net = sum(t["pnl"] for t in T)
print(f"balance at week start ~ {acc.balance - wk_net - cash_week:.2f}   closed P/L this week {wk_net:+.2f}")
print("\n=== PER BOT ===")
for b in ("SMC", "VP", "manual"):
    summ([t for t in T if t["bot"] == b], b)
for b in ("SMC", "VP"):
    print(f"\n  {b} by symbol / setup / exit:")
    rows = [t for t in T if t["bot"] == b]
    for s in sorted({t["sym"] for t in rows}):
        summ([t for t in rows if t["sym"] == s], "   " + s)
    for s in sorted({t["setup"] for t in rows if t["setup"]}):
        summ([t for t in rows if t["setup"] == s], "   setup " + s)
    for s in sorted({t["exit"] for t in rows}):
        summ([t for t in rows if t["exit"] == s], "   exit " + s)
print("\n=== PER DAY ===")
for dd in range((now_l.date() - WEEK.date()).days + 1):
    day = WEEK + timedelta(days=dd)
    rows = [t for t in T if day.timestamp() <= t["t_out"] < (day + timedelta(days=1)).timestamp()]
    by = {b: sum(t["pnl"] for t in rows if t["bot"] == b) for b in ("SMC", "VP", "manual")}
    print(f"  {day:%a %d/%m}: {len(rows):2d} trades  total {sum(by.values()):+8.2f}   "
          + "  ".join(f"{b} {v:+.2f}" for b, v in by.items() if any(t['bot'] == b for t in rows)))

print("\n=== ALL TRADES THIS WEEK ===")
def fr(v, fmt="+.2f", unit="R"):
    return "?" if v is None else f"{v:{fmt}}{unit}"


for t in T:
    print(f"  {lt(t['t_out']):%a %H:%M} {t['bot']:6s} {t['sym']:7s} {t['dir']:4s} {t['setup'] or '':12s} "
          f"{t['pnl']:+7.2f} ({fr(t['r'])})  best {fr(t['mfe'])}  held {t['held']:5.0f}m  {t['exit']}")

# ── today's losers: news + market direction ─────────────────────────────────
events = []
for ev in _fetch():
    if ev.get("impact") in ("High", "Medium"):
        dt = _parse_dt(ev)
        cur = COUNTRY_CURRENCY.get(ev.get("country", ""), (ev.get("country") or "")[:3].upper())
        if dt:
            events.append((dt, cur, ev.get("impact"), ev.get("title")))
print("\n=== TODAY'S LOSING TRADES: WHY ===")
for t in [t for t in T if t["t_out"] >= TODAY.timestamp() and t["pnl"] <= 0]:
    curs = _get_currencies(t["sym"])
    near = [e for e in events if e[1] in curs and
            t["t_in"] - 3600 <= e[0].timestamp() <= t["t_out"] + 900]
    d1 = mt5.copy_rates_range(t["sym"] + "m", mt5.TIMEFRAME_D1,
                              datetime.fromtimestamp(t["t_in"], UTC) - timedelta(days=1),
                              datetime.fromtimestamp(t["t_out"], UTC))
    day_move = ""
    if d1 is not None and len(d1):
        b = d1[-1]
        day_move = f"day {'UP' if b['close'] > b['open'] else 'DOWN'} {100 * (b['close'] - b['open']) / b['open']:+.2f}%"
    with_trend = ("with" if (t["dir"] == "buy") == ("UP" in day_move) else "AGAINST")
    print(f"  {lt(t['t_in']):%H:%M}->{lt(t['t_out']):%H:%M} {t['bot']} {t['sym']} {t['dir']} {t['setup']}  {t['pnl']:+.2f}  "
          f"best point {fr(t['mfe'])} after {fr(t['t_mfe'], '.0f', 'm')} | {day_move}, trade {with_trend} the day's move"
          + (" | NEWS: " + "; ".join(f"{e[0].astimezone():%H:%M} {e[1]} {e[2]} {e[3]}" for e in near) if near else ""))

# ── VP management on its REAL trades this week ──────────────────────────────
print("\n=== VP TRADE MANAGEMENT (real trades this week, replayed on M1 from the actual fill) ===")
vp = [t for t in T if t["bot"] == "VP" and t["sl0"] and t["risk"]]


def sim(t, tp=2.0, be=None, partial=None):
    buy = t["dir"] == "buy"
    rp = (t["entry"] - t["sl0"]) if buy else (t["sl0"] - t["entry"])
    # no time cap: some VP winners needed 54-65 h to reach their target
    m1 = mt5.copy_rates_range(t["sym"] + "m", mt5.TIMEFRAME_M1,
                              datetime.fromtimestamp(t["t_in"] - t["t_in"] % 60, UTC),
                              datetime.now(UTC))
    si = mt5.symbol_info(t["sym"] + "m")
    stop, pend, mfe, banked, size = -1.0, None, 0.0, 0.0, 1.0
    for b in m1:
        if pend is not None:
            stop, pend = max(stop, pend), None
        sp = b["spread"] * si.point
        fav = ((b["high"] - t["entry"]) if buy else (t["entry"] - (b["low"] + sp))) / rp
        adv = ((b["low"] - t["entry"]) if buy else (t["entry"] - (b["high"] + sp))) / rp
        if adv <= stop:
            return banked + size * stop
        if fav >= tp:
            return banked + size * tp
        mfe = max(mfe, fav)
        if partial and size == 1.0 and fav >= partial:
            banked, size, pend = 0.5 * partial, 0.5, 0.05
        if be and mfe >= be:
            pend = 0.05
    last = m1[-1]
    return banked + size * (((last["close"] - t["entry"]) if buy else (t["entry"] - last["close"])) / rp)


for t in vp:
    print(f"  {lt(t['t_in']):%a %H:%M} {t['sym']:7s} {t['dir']:4s} {t['setup']:9s} actual {t['pnl']:+7.2f} "
          f"({t['r']:+.2f}R, {t['exit']}), best point before closing {t['mfe']:+.2f}R, worst {t['mae']:+.2f}R")
rules = {"2R (current)": dict(tp=2.0), "2R + BE at +1R": dict(tp=2.0, be=1.0),
         "half at +1R -> 2R": dict(tp=2.0, partial=1.0), "fixed 1.5R": dict(tp=1.5),
         "fixed 1R": dict(tp=1.0)}
print(f"\n  {len(vp)} VP trades: actual ${sum(t['pnl'] for t in vp):+.2f}")
for name, kw in rules.items():
    rs = [sim(t, **kw) for t in vp]
    print(f"    {name:20s} ${sum(r * t['risk'] for r, t in zip(rs, vp)):+8.2f}  ({sum(rs):+.2f}R, "
          f"{sum(1 for r in rs if r > 0)}/{len(rs)} won)")
reach = lambda x: sum(1 for t in vp if t["mfe"] is not None and t["mfe"] >= x)
los = [t for t in vp if t["pnl"] <= 0]
print(f"  losers that were first at >= +0.5R: {sum(1 for t in los if t['mfe'] >= 0.5)}/{len(los)}, "
      f">= +1R: {sum(1 for t in los if t['mfe'] >= 1)}/{len(los)}; trades that reached +2R: {reach(2.0)}")
mt5.shutdown()
