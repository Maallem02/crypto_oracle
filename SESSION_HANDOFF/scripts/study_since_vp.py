"""Whole study since the Volume Profile bot went live (24/09 ~19:00 UTC) -> now.
Demo 262890622, read-only. Every closed position (SMC 234000, VP 234100,
manual 0): $ and R, path (MFE/MAE on M1), exit kind, settings era, momentum
at entry, overlaps between bots. Then management replays on the real trades."""
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import MetaTrader5 as mt5

UTC = timezone.utc
BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
BOT = {234000: "SMC", 234100: "VP", 0: "manual"}
START = datetime(2026, 9, 24, 18, 59, tzinfo=UTC)
HALF_LIVE = datetime(2026, 9, 29, 23, 0, tzinfo=UTC).timestamp()      # first half-at-1R deal 23:04
# VP settings eras (from the backend logs)
VP_ERAS = [(datetime(2026, 9, 24, 18, 59, tzinfo=UTC).timestamp(), "A all setups, 2/symbol"),
           (datetime(2026, 9, 28, 16, 30, tzinfo=UTC).timestamp(), "B re-entry only, 1/symbol"),
           (datetime(2026, 10, 2, 20, 31, tzinfo=UTC).timestamp(), "C + room filter, no XAG")]
db = sqlite3.connect(f"{BK}\\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
mt5.initialize(timeout=10000)
acc = mt5.account_info()
NOW = datetime.now(UTC)

deals = mt5.history_deals_get(START - timedelta(days=3), NOW + timedelta(hours=3)) or []
cash = [(d.time, d.profit) for d in deals if d.type not in (0, 1)]
after_start = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0) for d in deals if d.time >= START.timestamp())
bal0 = acc.balance - after_start
pos = defaultdict(list)
for d in deals:
    if d.type in (0, 1):
        pos[d.position_id].append(d)


def era_of(ts):
    e = VP_ERAS[0][1]
    for t0, name in VP_ERAS:
        if ts >= t0:
            e = name
    return e


def m1(sym, a, b):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, datetime.fromtimestamp(a - a % 60, UTC),
                             datetime.fromtimestamp(b, UTC))
    return r if r is not None and len(r) else None


T = []
for pid, ds in pos.items():
    full = sorted(mt5.history_deals_get(position=pid) or ds, key=lambda d: (d.time_msc, d.ticket))
    ins = [d for d in full if d.entry == 0]
    outs = [d for d in full if d.entry in (1, 3)]
    if not ins or not outs or sum(d.volume for d in outs) + 1e-9 < sum(d.volume for d in ins):
        continue
    i, last = ins[0], outs[-1]
    if i.time < START.timestamp():
        continue
    buy = i.type == 0
    bot = BOT.get(i.magic, "other")
    o = next(iter(mt5.history_orders_get(ticket=i.order) or []), None)
    sl0, tp0 = (o.sl, o.tp) if o is not None else (0, 0)
    setup = None
    vr = db.execute("SELECT setup, sl, tp, data FROM vp_log WHERE ticket=? AND status='taken'", (pid,)).fetchone()
    if vr:
        setup = {"va_reversal": "re-entry", "va_breakout": "breakout", "poc_bounce": "POC"}.get(vr["setup"], vr["setup"])
        sl0, tp0 = sl0 or vr["sl"], tp0 or vr["tp"]
    if not sl0:
        r2 = db.execute("SELECT sl, tp1 FROM trade_signals WHERE ticket=? ORDER BY id DESC", (pid,)).fetchone()
        if r2:
            sl0, tp0 = r2[0] or 0, r2[1] or 0
    if bot == "SMC":
        setup = "limit" if (i.comment or "").startswith("CryptoOracle-P") else "market"
    risk = -mt5.order_calc_profit(0 if buy else 1, i.symbol, i.volume, i.price, sl0) if sl0 else None
    pnl = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0) for d in full)
    costs = sum(d.commission + d.swap + getattr(d, "fee", 0) for d in full)
    c = (last.comment or "").lower()
    manual_close = last.reason in (0, 1, 2) and "half" not in c
    half = any("half" in (d.comment or "").lower() for d in outs)
    if manual_close:
        kind = "closed by you"
    elif "news" in c:
        kind = "news close"
    elif half:
        kind = "half@1R+" + ("target" if "[tp" in c else "BE stop" if "[sl" in c else "other")
    elif "[tp" in c:
        kind = "target"
    elif "[sl" in c:
        kind = "trail/BE stop" if pnl > 0 else "stop"
    else:
        kind = "other(" + str(last.reason) + ")"
    mfe = mae = None
    rp = ((i.price - sl0) if buy else (sl0 - i.price)) if sl0 else 0
    if rp > 0:
        bars = m1(i.symbol, i.time, last.time)
        if bars is not None:
            sp = bars["spread"] * mt5.symbol_info(i.symbol).point
            fav = ((bars["high"] - i.price) if buy else (i.price - (bars["low"] + sp))) / rp
            adv = ((bars["low"] - i.price) if buy else (i.price - (bars["high"] + sp))) / rp
            mfe, mae = float(fav.max()), float(adv.min())
    # momentum: price 24 h before entry (no look-ahead)
    h1 = mt5.copy_rates_from(i.symbol, mt5.TIMEFRAME_H1, datetime.fromtimestamp(i.time - 86400, UTC), 1)
    mom = None
    if h1 is not None and len(h1):
        chg = (i.price - h1[0]["close"]) / h1[0]["close"] * 100
        mom = "with 24h move" if (chg > 0) == buy else "against 24h move"
    bal_at = bal0 + sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0)
                        for d in deals if START.timestamp() <= d.time < i.time)
    T.append(dict(pid=pid, bot=bot, sym=i.symbol.rstrip("m"), dir="buy" if buy else "sell", setup=setup,
                  vol=i.volume, t_in=i.time, t_out=last.time, entry=i.price, sl0=sl0, tp0=tp0, risk=risk,
                  pnl=pnl, costs=costs, r=(pnl / risk if risk else None), exit=kind, mfe=mfe, mae=mae,
                  held=(last.time - i.time) / 60, mom=mom, hour=datetime.fromtimestamp(i.time, UTC).hour,
                  risk_pct=(100 * risk / bal_at if risk and bal_at > 0 else None),
                  era=era_of(i.time) if bot == "VP" else ("after half@1R" if i.time >= HALF_LIVE else "before half@1R"),
                  tp_r=(abs(tp0 - i.price) / rp if tp0 and rp else None)))
T.sort(key=lambda t: t["t_out"])


def summ(rows, label, w=34):
    if not rows:
        return
    p = np.array([t["pnl"] for t in rows])
    wn, ls = p[p > 0], p[p <= 0]
    rs = [t["r"] for t in rows if t["r"] is not None]
    pf = wn.sum() / -ls.sum() if ls.sum() else float("inf")
    print(f"  {label:{w}s} {len(p):3d} tr  WR {100 * len(wn) / len(p):3.0f}%  net {p.sum():+8.2f}  "
          f"avg W {wn.mean() if len(wn) else 0:+6.2f} L {ls.mean() if len(ls) else 0:+6.2f}  "
          f"PF {pf:4.2f}  avgR {np.mean(rs) if rs else 0:+.2f}  sumR {np.sum(rs) if rs else 0:+.1f}")


def maxdd(vals):
    eq = np.cumsum([0.0] + list(vals))
    return float((eq - np.maximum.accumulate(eq)).min())


def streak(rows):
    best = cur = 0
    for t in rows:
        cur = cur + 1 if t["pnl"] <= 0 else 0
        best = max(best, cur)
    return best


print(f"PERIOD {START:%d/%m %H:%M} -> {NOW:%d/%m %H:%M} UTC ({(NOW - START).days} days)")
print(f"balance at start {bal0:.2f} | now {acc.balance:.2f} | cash moves {sum(x for _, x in cash):+.2f} "
      f"| closed trading P/L {sum(t['pnl'] for t in T):+.2f} | commission+swap {sum(t['costs'] for t in T):+.2f}")
# balance path (closed P/L only) for peak / trough
ev = sorted([(t["t_out"], t["pnl"]) for t in T] + cash)
b, pk, pk_t, trough = bal0, bal0, START.timestamp(), (0, None)
for ts, x in ev:
    b += x
    if b > pk:
        pk, pk_t = b, ts
print(f"highest closed balance {pk:.2f} on {datetime.fromtimestamp(pk_t, UTC):%a %d/%m %H:%M}")

print("\n=== PER BOT ===")
for bt in ("SMC", "VP", "manual"):
    rows = [t for t in T if t["bot"] == bt]
    summ(rows, bt)
    if rows:
        print(f"      max drawdown (closed) {maxdd([t['pnl'] for t in rows]):+.2f}   longest losing streak {streak(rows)}"
              f"   avg risk/trade ${np.mean([t['risk'] for t in rows if t['risk']]):.2f}"
              f" = {np.mean([t['risk_pct'] for t in rows if t['risk_pct']]):.1f}% of balance")

print("\n=== PER DAY (UTC) ===")
d0 = START.date()
while d0 <= NOW.date():
    a = datetime(d0.year, d0.month, d0.day, tzinfo=UTC).timestamp()
    rows = [t for t in T if a <= t["t_out"] < a + 86400]
    if rows:
        by = {bt: [t for t in rows if t["bot"] == bt] for bt in ("SMC", "VP", "manual")}
        print(f"  {d0:%a %d/%m}  total {sum(t['pnl'] for t in rows):+7.2f}   " + "   ".join(
            f"{bt} {sum(t['pnl'] for t in r):+7.2f} ({sum(1 for t in r if t['pnl'] > 0)}/{len(r)})"
            for bt, r in by.items() if r))
    d0 += timedelta(days=1)

for bt in ("SMC", "VP"):
    rows = [t for t in T if t["bot"] == bt]
    print(f"\n=== {bt} BREAKDOWN ===")
    for key, lab in (("era", "era"), ("sym", "symbol"), ("dir", "direction"), ("setup", "setup"),
                     ("exit", "exit"), ("mom", "momentum")):
        for v in sorted({str(t[key]) for t in rows}):
            summ([t for t in rows if str(t[key]) == v], f"{lab}: {v}")
        print()
    for a, b2 in ((0, 6), (6, 12), (12, 18), (18, 24)):
        summ([t for t in rows if a <= t["hour"] < b2], f"hour UTC {a:02d}-{b2:02d}")
    print()
    for s in sorted({t["sym"] for t in rows}):
        for dr in ("buy", "sell"):
            summ([t for t in rows if t["sym"] == s and t["dir"] == dr], f"{s} {dr}")
    los = [t for t in rows if t["pnl"] <= 0 and t["mfe"] is not None]
    print(f"\n  losers: {len(los)} | had been at >= +0.5R first: {sum(t['mfe'] >= .5 for t in los)} "
          f"| >= +1R: {sum(t['mfe'] >= 1 for t in los)}")
    win = [t for t in rows if t["pnl"] > 0 and t["mfe"] is not None]
    if win:
        print(f"  winners: {len(win)} | median best point {np.median([t['mfe'] for t in win]):+.2f}R "
              f"| median kept {np.median([t['r'] for t in win if t['r'] is not None]):+.2f}R")
    print(f"  median hold: winners {np.median([t['held'] for t in rows if t['pnl'] > 0] or [0]):.0f} min, "
          f"losers {np.median([t['held'] for t in rows if t['pnl'] <= 0] or [0]):.0f} min")
    tps = [t["tp_r"] for t in rows if t["tp_r"]]
    if tps:
        print(f"  target distance: median {np.median(tps):.2f}R")

print("\n=== SMC: TWO TRADES AT ONCE (BTC + ETH, same direction, entries < 30 min apart) ===")
smc = [t for t in T if t["bot"] == "SMC"]
pairs = []
for a_ in smc:
    for b_ in smc:
        if a_["pid"] < b_["pid"] and a_["sym"] != b_["sym"] and a_["dir"] == b_["dir"] \
                and abs(a_["t_in"] - b_["t_in"]) < 1800:
            pairs.append((a_, b_))
both_l = sum(1 for a_, b_ in pairs if a_["pnl"] <= 0 and b_["pnl"] <= 0)
both_w = sum(1 for a_, b_ in pairs if a_["pnl"] > 0 and b_["pnl"] > 0)
print(f"  {len(pairs)} pairs: both lost {both_l}, both won {both_w}, split {len(pairs) - both_l - both_w}, "
      f"net {sum(a_['pnl'] + b_['pnl'] for a_, b_ in pairs):+.2f}")
for a_, b_ in pairs:
    print(f"    {datetime.fromtimestamp(a_['t_in'], UTC):%d/%m %H:%M} {a_['dir']} {a_['sym']} {a_['pnl']:+.2f} + "
          f"{b_['sym']} {b_['pnl']:+.2f}")

print("\n=== BOTH BOTS ON THE SAME SYMBOL AT THE SAME TIME ===")
vp = [t for t in T if t["bot"] == "VP"]
ov = [(s, v) for s in smc for v in vp if s["sym"] == v["sym"] and s["t_in"] < v["t_out"] and v["t_in"] < s["t_out"]]
same = [(s, v) for s, v in ov if s["dir"] == v["dir"]]
print(f"  {len(ov)} overlaps: same direction {len(same)} (double risk), opposite {len(ov) - len(same)} (hedged)")
for s, v in ov:
    print(f"    {s['sym']} SMC {s['dir']} {s['pnl']:+.2f} / VP {v['dir']} {v['pnl']:+.2f}  "
          f"({datetime.fromtimestamp(max(s['t_in'], v['t_in']), UTC):%d/%m %H:%M})")


# ── management replays on the REAL trades ───────────────────────────────────
def sim(t, tp, be=None, partial=None, stop_at=None):
    buy = t["dir"] == "buy"
    rp = (t["entry"] - t["sl0"]) if buy else (t["sl0"] - t["entry"])
    bars = m1(t["sym"] + "m", t["t_in"], NOW.timestamp())
    if bars is None or rp <= 0:
        return None
    pt = mt5.symbol_info(t["sym"] + "m").point
    stop, pend, mfe, banked, size = -1.0, None, 0.0, 0.0, 1.0
    for bar in bars:
        if pend is not None:
            stop, pend = max(stop, pend), None
        sp = bar["spread"] * pt
        fav = ((bar["high"] - t["entry"]) if buy else (t["entry"] - (bar["low"] + sp))) / rp
        adv = ((bar["low"] - t["entry"]) if buy else (t["entry"] - (bar["high"] + sp))) / rp
        if adv <= stop:
            return banked + size * stop
        if fav >= tp:
            return banked + size * tp
        mfe = max(mfe, fav)
        if partial and size == 1.0 and fav >= partial:
            banked, size, pend = 0.5 * partial, 0.5, 0.05
        if be and mfe >= be:
            pend = max(pend or -9, 0.05)
    lb = bars[-1]
    return banked + size * (((lb["close"] - t["entry"]) if buy else (t["entry"] - lb["close"])) / rp)


print("\n=== MANAGEMENT REPLAY ON THE REAL TRADES (M1 from the actual fill, stop first on ties) ===")
for bt, rows, rules in (
        ("VP", [t for t in vp if t["sl0"] and t["risk"]],
         {"fixed 2R (live rule)": dict(tp=2.0), "2R + BE at +1R": dict(tp=2.0, be=1.0),
          "half at +1R -> 2R": dict(tp=2.0, partial=1.0), "fixed 1.5R": dict(tp=1.5), "fixed 1R": dict(tp=1.0),
          "fixed 3R": dict(tp=3.0)}),
        ("SMC", [t for t in smc if t["sl0"] and t["risk"] and t["tp_r"]],
         {"full to its target, no BE": "tp", "half at +1R -> target (live since 30/09)": "half",
          "BE at +1R -> target": "be", "fixed 1R": dict(tp=1.0), "fixed 1.5R": dict(tp=1.5)})):
    actual = sum(t["pnl"] for t in rows)
    print(f"  {bt}: {len(rows)} trades, actual ${actual:+.2f}")
    for name, kw in rules.items():
        res = []
        for t in rows:
            if kw == "tp":
                res.append(sim(t, tp=t["tp_r"]))
            elif kw == "half":
                res.append(sim(t, tp=t["tp_r"], partial=1.0))
            elif kw == "be":
                res.append(sim(t, tp=t["tp_r"], be=1.0))
            else:
                res.append(sim(t, **kw))
        ok = [(r, t) for r, t in zip(res, rows) if r is not None]
        print(f"    {name:42s} ${sum(r * t['risk'] for r, t in ok):+8.2f}  ({sum(r for r, _ in ok):+6.1f}R, "
              f"{sum(1 for r, _ in ok if r > 0)}/{len(ok)} won)")

print("\n=== VP signals the room filter skipped (replayed at fixed 2R from the signal entry) ===")
sk = db.execute("SELECT timestamp, symbol, direction, entry, sl, tp, reason FROM vp_log WHERE status='rejected' "
                "AND reason LIKE 'value area too narrow%' ORDER BY id").fetchall()
tot = 0
for r in sk:
    ts = datetime.fromisoformat(r["timestamp"]).timestamp()
    fake = dict(sym=r["symbol"], dir=r["direction"], entry=r["entry"], sl0=r["sl"], t_in=ts)
    res = sim(fake, tp=2.0)
    tot += res or 0
    print(f"  {r['timestamp'][:16]} {r['symbol']:7s} {r['direction']:4s} -> {res if res is None else round(res, 2)}R")
print(f"  total {tot:+.2f}R (positive = the filter cost money, negative = it saved money)")

print("\n=== EVERY TRADE ===")
def f(v, fmt):
    return "?" if v is None else format(v, fmt)


for t in T:
    print(f"  {datetime.fromtimestamp(t['t_in'], UTC):%d/%m %H:%M} {t['bot']:6s} {t['sym']:7s} {t['dir']:4s} "
          f"{(t['setup'] or ''):9s} {t['pnl']:+7.2f} ({f(t['r'], '+.2f')}R) best {f(t['mfe'], '+.2f')}R  "
          f"risk {f(t['risk_pct'], '.1f')}%  {t['held']:6.0f}m  {t['exit']}  [{t['mom']}]")
json.dump(T, open(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\SESSION_HANDOFF\scripts\study_trades.json", "w"), default=str)
mt5.shutdown()
