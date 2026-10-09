"""Trade-management study on REAL closed trades (demo 262890622). Read-only.

For every closed bot trade: actual fill, ORIGINAL stop/target (from the opening
order), the minute-by-minute path from MT5 M1 bars, then alternative closing
rules replayed on that exact path.

Conventions (conservative):
  - M1 bars are BID. Buys: stop if low <= SL, target if high >= TP. Sells: the
    exit is at ASK, so bar prices + that bar's spread.
  - Same bar touches stop and target -> STOP first.
  - A stop moved by a rule (break-even, lock, trail) applies from the NEXT bar.
  - Unresolved after 48 h -> exit at market. R = |entry - original stop|.
  - $ results = R x the trade's real $-risk at its original stop.
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import MetaTrader5 as mt5

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
SINCE = datetime(2026, 8, 29, tzinfo=UTC)
HOLD = 48 * 3600
BOT = {234000: "SMC", 234100: "VP"}

import sqlite3
_db = sqlite3.connect(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db")
mt5.initialize(timeout=10000)
now = datetime.now(UTC)

# ── closed trades with their original bracket ────────────────────────────────
deals = mt5.history_deals_get(SINCE, now + timedelta(hours=3)) or []
by_pos = defaultdict(list)
for d in deals:
    if d.type in (0, 1):
        by_pos[d.position_id].append(d)
trades, skipped = [], defaultdict(int)
for pid, ds in by_pos.items():
    ins = [d for d in ds if d.entry == 0]
    outs = [d for d in ds if d.entry in (1, 3)]
    if not ins or not outs or ins[0].magic not in BOT:
        continue
    i, last = ins[0], max(outs, key=lambda d: d.time)
    orders = mt5.history_orders_get(position=pid) or []
    o = next((x for x in orders if x.ticket == i.order), None) or (orders[0] if orders else None)
    sl0, tp0 = (o.sl, o.tp) if o is not None else (0.0, 0.0)
    if not sl0:
        # filled LIMIT orders: MT5 history keeps sl/tp = 0 -> the bot's own record
        row = _db.execute("SELECT sl, tp1 FROM trade_signals WHERE ticket IN (?, ?) "
                          "ORDER BY id DESC LIMIT 1", (pid, i.order)).fetchone()
        if row and row[0]:
            sl0, tp0 = float(row[0]), float(row[1] or 0)
            skipped["(limit trades: stop taken from bot DB)"] += 1
    if not sl0:
        skipped["no original stop"] += 1
        continue
    buy = i.type == 0
    entry = i.price
    rp = (entry - sl0) if buy else (sl0 - entry)
    if rp <= 0:
        skipped["stop on wrong side of fill"] += 1
        continue
    risk_usd = -mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                                      i.symbol, i.volume, entry, sl0)
    if not risk_usd or risk_usd <= 0:
        skipped["no $ risk"] += 1
        continue
    profit = sum(x.profit + x.commission + x.swap for x in ds)
    trades.append(dict(pid=pid, bot=BOT[i.magic], sym=i.symbol.rstrip("m"), buy=buy,
                       t_in=i.time, t_out=last.time, entry=entry, sl0=sl0, tp0=tp0, rp=rp,
                       risk=risk_usd, profit=profit, r_act=profit / risk_usd,
                       comment=i.comment, exit_comment=last.comment or ""))
trades.sort(key=lambda t: t["t_in"])
print(f"{len(trades)} closed bot trades since {SINCE:%d/%m} (skipped: {dict(skipped)})")

# ── price paths ──────────────────────────────────────────────────────────────
bars, atr_at = {}, {}
for s in sorted({t["sym"] for t in trades}):
    ms = s + "m"
    si = mt5.symbol_info(ms)
    m1 = mt5.copy_rates_range(ms, mt5.TIMEFRAME_M1, SINCE - timedelta(days=1), now)
    m15 = mt5.copy_rates_range(ms, mt5.TIMEFRAME_M15, SINCE - timedelta(days=5), now)
    bars[s] = (m1, si.point)
    atr_at[s] = (m15["time"], atr_series(m15))
mt5.shutdown()


def path(t):
    m1, point = bars[t["sym"]]
    a = np.searchsorted(m1["time"], t["t_in"] - t["t_in"] % 60)
    b = np.searchsorted(m1["time"], t["t_in"] + HOLD)
    return m1[a:b], point


def atr_entry(t):
    ts, at = atr_at[t["sym"]]
    k = np.searchsorted(ts, t["t_in"] - 900, side="right") - 1      # last CLOSED M15 bar
    return float(at[k]) if k >= 0 else t["rp"]


def fav_adv(t, b, point):
    """favourable / adverse extremes of one bar, in R, from the trade's side."""
    e, rp = t["entry"], t["rp"]
    if t["buy"]:
        return (b["high"] - e) / rp, (b["low"] - e) / rp
    sp = b["spread"] * point
    return (e - (b["low"] + sp)) / rp, (e - (b["high"] + sp)) / rp


def simulate(t, rule):
    """-> R result of `rule` on this trade's path."""
    p, point = path(t)
    if len(p) == 0:
        return t["r_act"]
    rp = t["rp"]
    tp_r = rule.get("tp")
    if tp_r == "orig":
        tp_r = abs(t["tp0"] - t["entry"]) / rp if t["tp0"] else None
    stop = -1.0                       # stop level, in R
    pending = None                    # stop change applying from next bar
    banked, size = 0.0, 1.0
    mfe = 0.0
    atr_r = atr_entry(t) / rp
    t0 = int(p[0]["time"])
    for b in p:
        if pending is not None:
            stop = max(stop, pending)
            pending = None
        fav, adv = fav_adv(t, b, point)
        el = (int(b["time"]) - t0) / 3600
        if adv <= stop:
            return banked + size * stop
        if tp_r is not None and fav >= tp_r:
            return banked + size * tp_r
        if rule.get("max_h") and el >= rule["max_h"]:
            close_r = fav_adv(t, dict(high=b["close"], low=b["close"], spread=b["spread"]), point)[0]
            return banked + size * close_r
        if rule.get("time_h") and el >= rule["time_h"] and mfe < rule.get("time_need", 0.5):
            close_r = fav_adv(t, dict(high=b["close"], low=b["close"], spread=b["spread"]), point)[0]
            return banked + size * close_r
        mfe = max(mfe, fav)
        if rule.get("partial") and size == 1.0 and fav >= rule["partial"]:
            banked, size = 0.5 * rule["partial"], 0.5
            pending = max(pending or -9, 0.05)
        if rule.get("be") and mfe >= rule["be"]:
            pending = max(pending or -9, 0.05)            # entry + a hair for the spread
        if rule.get("lock"):                              # ladder: lock (mfe - step), in steps
            step = rule["lock"]
            if mfe >= 2 * step:
                pending = max(pending or -9, np.floor(mfe / step) * step - step)
        if rule.get("giveback") and mfe >= rule.get("gb_arm", 1.0):
            pending = max(pending or -9, mfe * (1 - rule["giveback"]))
        if rule.get("atr_trail") and mfe >= rule.get("arm", 1.0):
            pending = max(pending or -9, mfe - rule["atr_trail"] * atr_r)
        if rule.get("smc"):
            # router.manage_open_positions as it runs today, sampled on bar closes:
            # arm at 1R (ETH 1.5R); stop = price - max(5% of TP distance, 10% of
            # profit), floored at 1.5xATR (XAU) / 3xATR (ETH); break-even at 1.5R
            close_r = fav_adv(t, dict(high=b["close"], low=b["close"], spread=b["spread"]), point)[0]
            arm = 1.5 if "ETH" in t["sym"] else 1.0
            floor = 3.0 if "ETH" in t["sym"] else (1.5 if "XAU" in t["sym"] else 0.0)
            if close_r >= arm:
                buf = max(0.05 * (tp_r or 2.5), 0.10 * close_r)
                if floor and floor * atr_r > buf:
                    buf = floor * atr_r
                pending = max(pending or -9, close_r - buf)
            elif close_r >= 1.5:
                pending = max(pending or -9, 0.0)
    last = p[-1]
    return banked + size * fav_adv(t, dict(high=last["close"], low=last["close"],
                                           spread=last["spread"]), point)[0]


# ── MFE / giveback facts on the ACTUAL trades ────────────────────────────────
for t in trades:
    p, point = path(t)
    live = p[p["time"] <= t["t_out"]]
    fav = [fav_adv(t, b, point)[0] for b in live] or [0.0]
    k = int(np.argmax(fav))
    t["mfe"] = max(0.0, max(fav))
    t["min_to_mfe"] = (int(live[k]["time"]) - t["t_in"]) / 60 if len(live) else 0
    t["held_min"] = (t["t_out"] - t["t_in"]) / 60


def facts(rows, label):
    if not rows:
        return
    los = [t for t in rows if t["profit"] <= 0]
    win = [t for t in rows if t["profit"] > 0]
    print(f"\n{label}: {len(rows)} trades, {len(win)} wins, net ${sum(t['profit'] for t in rows):+.2f}")
    for th in (0.5, 1.0, 1.5):
        n = sum(1 for t in los if t["mfe"] >= th)
        print(f"   losers that were at >= +{th}R before closing at a loss: {n}/{len(los)} "
              f"({100 * n / len(los):.0f}%)" if los else "")
    if win:
        gb = [t["mfe"] - t["r_act"] for t in win]
        print(f"   winners: best point avg +{np.mean([t['mfe'] for t in win]):.2f}R, closed avg "
              f"+{np.mean([t['r_act'] for t in win]):.2f}R (gave back {np.mean(gb):.2f}R)")
    print(f"   time to best point: winners median {np.median([t['min_to_mfe'] for t in win]) if win else 0:.0f} min, "
          f"losers median {np.median([t['min_to_mfe'] for t in los]) if los else 0:.0f} min; "
          f"held: winners {np.median([t['held_min'] for t in win]) if win else 0:.0f} / "
          f"losers {np.median([t['held_min'] for t in los]) if los else 0:.0f} min")


RULES = {
    "ACTUAL (what happened)": None,
    "original stop+target, no mgmt": dict(tp="orig"),
    "SMC CURRENT trail (as coded today)": dict(tp="orig", smc=True),
    "fixed 1R": dict(tp=1.0),
    "fixed 1.5R": dict(tp=1.5),
    "fixed 2R": dict(tp=2.0),
    "orig TP + BE at +0.5R": dict(tp="orig", be=0.5),
    "orig TP + BE at +1R": dict(tp="orig", be=1.0),
    "orig TP + lock ladder 0.5R": dict(tp="orig", lock=0.5),
    "orig TP + keep 50% of best (from +1R)": dict(tp="orig", giveback=0.5, gb_arm=1.0),
    "orig TP + keep 50% of best (from +0.5R)": dict(tp="orig", giveback=0.5, gb_arm=0.5),
    "half at +1R, rest BE -> orig TP": dict(tp="orig", partial=1.0),
    "orig TP + 1xATR trail from +1R": dict(tp="orig", atr_trail=1.0, arm=1.0),
    "orig TP, exit if < +0.5R after 2h": dict(tp="orig", time_h=2, time_need=0.5),
    "orig TP, close after 8h": dict(tp="orig", max_h=8),
    "2R + keep 50% of best (from +1R)": dict(tp=2.0, giveback=0.5, gb_arm=1.0),
}


def table(rows, label):
    if not rows:
        return
    mid = rows[len(rows) // 2]["t_in"]
    print(f"\n{label} — {len(rows)} trades  (halves split {datetime.fromtimestamp(mid, UTC):%d/%m})")
    print(f"   {'rule':42s} {'total $':>9s} {'avg R':>7s} {'WR':>5s} {'maxDD $':>8s}   1st half / 2nd half $")
    for name, rule in RULES.items():
        rs = [t["r_act"] if rule is None else simulate(t, rule) for t in rows]
        usd = np.array([r * t["risk"] for r, t in zip(rs, rows)])
        eq = np.cumsum(usd)
        dd = (np.maximum.accumulate(np.concatenate(([0], eq)))[1:] - eq).max()
        h1 = usd[[t["t_in"] < mid for t in rows]].sum()
        h2 = usd[[t["t_in"] >= mid for t in rows]].sum()
        print(f"   {name:42s} {usd.sum():+9.2f} {np.mean(rs):+7.2f} {100 * np.mean(np.array(rs) > 0):4.0f}% "
              f"{dd:8.2f}   {h1:+8.2f} / {h2:+8.2f}")


smc = [t for t in trades if t["bot"] == "SMC"]
smc_now = [t for t in smc if t["sym"] != "XAUUSD"]            # gold is blocked for SMC now
vp = [t for t in trades if t["bot"] == "VP"]
facts(smc_now, "SCALPING bot (BTC/ETH/XAG — gold excluded, blocked since)")
facts(vp, "VOLUME PROFILE bot (all its live trades)")
table(smc_now, "SCALPING bot")
table(vp, "VOLUME PROFILE bot")
for s in sorted({t["sym"] for t in smc_now}):
    rows = [t for t in smc_now if t["sym"] == s]
    print(f"   SMC {s}: {len(rows)} trades, actual ${sum(t['profit'] for t in rows):+.2f}, "
          f"'BE at +0.5R' ${sum(simulate(t, RULES['orig TP + BE at +0.5R']) * t['risk'] for t in rows):+.2f}, "
          f"'keep 50% from +1R' ${sum(simulate(t, RULES['orig TP + keep 50% of best (from +1R)']) * t['risk'] for t in rows):+.2f}")
