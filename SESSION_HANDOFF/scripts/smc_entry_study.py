"""WHY DO SCALPING (SMC, magic 234000) ENTRIES FAIL?  Read-only, demo 262890622.

Every SMC position opened since 25/07. For each one:
  * path after the fill on M1 (spread charged, stop first on ties):
      - first touch +T before -S for a grid of targets/stops in the trade's own R
      - close-based move at +15m / +1h / +4h / +24h, in R, in the trade direction
  * the same entry in the OPPOSITE direction (mirror)
  * a RANDOM baseline: 30 random times within +-5 days, same symbol, same
    direction, same stop size in ATR -> "is the timing better than random?"
  * context at the signal (only bars closed BEFORE it, no look-ahead):
      move over the previous 1 h / 4 h in ATR (signed with the trade), stretch
      from EMA50, H1 / H4 trend vs the trade, place in the last 24 h range,
      stop size in ATR, spread in R, how far into its candle the signal fired,
      whether the signal candle then CLOSED for or against the trade
  * the bot's own scores (trade_signals): score, ADX, RSI, P/D, HTF consensus, ...
Then: does the entry have a direction edge at all, is it the stop, the target,
the timing, and which features separate winners from losers in BOTH halves."""
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import MetaTrader5 as mt5

UTC = timezone.utc
BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
OUT = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\SESSION_HANDOFF\scripts\smc_entry_rows.json"
D0 = datetime(2026, 7, 31, tzinfo=UTC)
M1_FROM = datetime(2026, 7, 29, 19, tzinfo=UTC)
NOW = datetime.now(UTC)
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}
GRID_T = (0.5, 1.0, 1.5, 2.0, 3.0)
GRID_S = (0.5, 1.0, 1.5, 2.0, 3.0)
HORIZON = 24 * 3600
rng = np.random.default_rng(7)

db = sqlite3.connect(f"{BK}\\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
mt5.initialize(timeout=10000)

_cache = {}


def bars(sym, tf, a, b):
    """M1/M15/H1/H4 bars [a, b] (epoch), cached per symbol+tf by day blocks."""
    key = (sym, tf)
    if key not in _cache:
        # the terminal keeps 100 000 bars: crypto M1 only reaches 29/07 18:00
        a0 = M1_FROM if tf == mt5.TIMEFRAME_M1 else D0 - timedelta(days=70)
        r = mt5.copy_rates_range(sym, tf, a0, NOW + timedelta(hours=1))
        if r is None:
            raise RuntimeError(f"no bars {sym} {tf}: {mt5.last_error()}")
        _cache[key] = r
    r = _cache[key]
    i, j = np.searchsorted(r["time"], a), np.searchsorted(r["time"], b, side="right")
    return r[i:j]


def atr14(r):
    if len(r) < 15:
        return None
    h, l, c = r["high"], r["low"], r["close"]
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    a = tr[:14].mean()
    for x in tr[14:]:
        a = (a * 13 + x) / 14
    return float(a)


def ema(x, n):
    k, e = 2 / (n + 1), float(x[0])
    for v in x[1:]:
        e = v * k + e * (1 - k)
    return e


def path(sym, t0, entry, buy, rp, point):
    """first-touch grid + timed moves, from the fill. rp = 1R in price."""
    m = bars(sym, mt5.TIMEFRAME_M1, t0 - t0 % 60, t0 + HORIZON)
    if len(m) < 5 or rp <= 0:
        return None
    sp = m["spread"] * point
    fav = ((m["high"] - entry) if buy else (entry - (m["low"] + sp))) / rp
    adv = ((m["low"] - entry) if buy else (entry - (m["high"] + sp))) / rp
    grid = {}
    for S in GRID_S:
        hs = np.nonzero(adv <= -S)[0]
        i_s = hs[0] if len(hs) else 10 ** 9
        for T in GRID_T:
            ht = np.nonzero(fav >= T)[0]
            i_t = ht[0] if len(ht) else 10 ** 9
            if i_t < i_s:
                grid[f"{T}/{S}"] = T
            elif i_s < 10 ** 9:
                grid[f"{T}/{S}"] = -S
            else:                                  # neither in 24 h: close at the horizon
                c = m["close"][-1]
                grid[f"{T}/{S}"] = float(((c - entry) if buy else (entry - c - sp[-1])) / rp)
    mv = {}
    for lab, sec in (("15m", 900), ("1h", 3600), ("4h", 14400), ("24h", 86400)):
        k = np.searchsorted(m["time"], t0 + sec) - 1
        if 0 <= k < len(m) and m["time"][k] >= t0 + sec - 600:
            c = m["close"][k]
            mv[lab] = float(((c - entry) if buy else (entry - c)) / rp)
    return grid, mv, float(fav.max()), float(adv.min())


# ── the trades ───────────────────────────────────────────────────────────────
deals = mt5.history_deals_get(D0 - timedelta(days=2), NOW + timedelta(hours=3)) or []
pos = defaultdict(list)
for d in deals:
    if d.type in (0, 1):
        pos[d.position_id].append(d)
rows = []
for pid, ds in pos.items():
    full = sorted(mt5.history_deals_get(position=pid) or ds, key=lambda d: (d.time_msc, d.ticket))
    ins = [d for d in full if d.entry == 0]
    outs = [d for d in full if d.entry in (1, 3)]
    if not ins or ins[0].magic != 234000 or ins[0].time < D0.timestamp():
        continue
    if not outs or sum(d.volume for d in outs) + 1e-9 < sum(d.volume for d in ins):
        continue
    i, last = ins[0], outs[-1]
    buy = i.type == 0
    sym = i.symbol
    si = mt5.symbol_info(sym)
    ts = db.execute("SELECT * FROM trade_signals WHERE ticket=? ORDER BY id DESC", (pid,)).fetchone()
    o = next(iter(mt5.history_orders_get(ticket=i.order) or []), None)
    sl0 = (ts["sl"] if ts and ts["sl"] else 0) or (o.sl if o else 0)
    tp0 = (ts["tp1"] if ts and ts["tp1"] else 0) or (o.tp if o else 0)
    if not sl0:
        continue
    rp = (i.price - sl0) if buy else (sl0 - i.price)
    if rp <= 0:
        continue
    limit = (i.comment or "").startswith("CryptoOracle-P") or (o is not None and o.type in (2, 3))
    t_sig = int(o.time_setup) if (o is not None and limit) else i.time
    tf = (ts["timeframe"] if ts and ts["timeframe"] else "15m")
    pnl = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0) for d in full)
    risk_usd = -mt5.order_calc_profit(0 if buy else 1, sym, i.volume, i.price, sl0)
    p = path(sym, i.time, i.price, buy, rp, si.point)
    if p is None:
        continue
    grid, mv, mfe, mae = p
    # opposite side at the same moment pays the spread too: the bot bought at
    # the ask, the mirror sells at the bid (and vice versa)
    _fm = bars(sym, mt5.TIMEFRAME_M1, i.time - i.time % 60, i.time - i.time % 60)
    _spe = float(_fm["spread"][0]) * si.point if len(_fm) else 0.0
    mirror = path(sym, i.time, i.price - _spe if buy else i.price + _spe, not buy, rp, si.point)
    # context, bars closed before the signal
    m15 = bars(sym, mt5.TIMEFRAME_M15, t_sig - 6 * 86400, t_sig - 900)
    h1 = bars(sym, mt5.TIMEFRAME_H1, t_sig - 20 * 86400, t_sig - 3600)
    h4 = bars(sym, mt5.TIMEFRAME_H4, t_sig - 60 * 86400, t_sig - 4 * 3600)
    m1s = bars(sym, mt5.TIMEFRAME_M1, t_sig - 86400, t_sig)
    a15 = atr14(m15[-60:]) if len(m15) else None
    sgn = 1 if buy else -1
    px = float(m1s["close"][-1]) if len(m1s) else i.price
    ctx = {}
    if a15:
        def back(sec):
            k = np.searchsorted(m1s["time"], t_sig - sec)
            return float(m1s["close"][k]) if k < len(m1s) else None
        for lab, sec in (("mom1h", 3600), ("mom4h", 14400)):
            b0 = back(sec)
            ctx[lab] = sgn * (px - b0) / a15 if b0 else None
        ctx["ema50_15m"] = sgn * (px - ema(m15["close"][-200:], 50)) / a15 if len(m15) >= 60 else None
        ctx["stop_atr"] = rp / a15
        hi, lo = float(m1s["high"].max()), float(m1s["low"].min())
        ctx["range24"] = ((px - lo) / (hi - lo) if buy else (hi - px) / (hi - lo)) if hi > lo else None
    for lab, r_ in (("h1", h1), ("h4", h4)):
        if len(r_) >= 60:
            e_now, e_prev = ema(r_["close"][-120:], 50), ema(r_["close"][-125:-5], 50)
            ctx[f"{lab}_trend"] = "with" if (e_now > e_prev) == buy else "against"
            ctx[f"{lab}_side"] = "with" if (px > e_now) == buy else "against"
    # the signal candle: how far into it, and did it close for / against the trade
    sec = TF_SEC.get(tf, 900)
    c_open = t_sig - t_sig % sec
    ctx["into_candle"] = (t_sig - c_open) / sec
    sc = bars(sym, mt5.TIMEFRAME_M1, c_open, c_open + sec - 60)
    if len(sc):
        ctx["candle_close_R"] = sgn * (float(sc["close"][-1]) - px) / rp
    spr = float(np.median(bars(sym, mt5.TIMEFRAME_M1, t_sig - 600, t_sig)["spread"] if len(m1s) else [0])) * si.point
    ctx["spread_R"] = spr / rp
    ctx["fill_delay_min"] = (i.time - t_sig) / 60
    # random baseline: same symbol, same direction, same stop in ATR, +-5 days
    base = []
    if a15 and ctx.get("stop_atr"):
        m1w = bars(sym, mt5.TIMEFRAME_M1, i.time - 5 * 86400, min(i.time + 5 * 86400, NOW.timestamp() - HORIZON))
        if len(m1w) > 500:
            for k in rng.choice(len(m1w) - 1, size=min(30, len(m1w) - 1), replace=False):
                tb = int(m1w["time"][k])
                mm = bars(sym, mt5.TIMEFRAME_M15, tb - 3 * 86400, tb - 900)
                aa = atr14(mm[-60:]) if len(mm) else None
                if not aa:
                    continue
                e = float(m1w["close"][k]) + (float(m1w["spread"][k]) * si.point if buy else 0)
                pb = path(sym, tb + 60, e, buy, ctx["stop_atr"] * aa, si.point)
                if pb:
                    base.append(pb[0]["1.0/1.0"])
    rows.append(dict(
        pid=pid, sym=sym.rstrip("m"), dir="buy" if buy else "sell", tf=tf, limit=limit,
        t_sig=t_sig, t_in=i.time, t_out=last.time, entry=i.price, sl0=sl0, tp0=tp0,
        tp_r=abs(tp0 - i.price) / rp if tp0 else None, pnl=pnl, risk_usd=risk_usd,
        r=pnl / risk_usd if risk_usd > 0 else None, mfe=mfe, mae=mae, grid=grid, mv=mv,
        mirror=mirror[0] if mirror else None, mirror_mv=mirror[1] if mirror else None,
        base=base, ctx=ctx,
        sig={k: ts[k] for k in ("scalping_score", "adx", "atr_ratio", "rsi", "stoch_k", "structure",
                                "lg_strength", "pd_zone", "pd_pct", "htf_consensus", "smc_confluence",
                                "volume_ratio", "spread_pct", "rr_ratio", "hour")} if ts else {},
    ))
mt5.shutdown()
json.dump(rows, open(OUT, "w"), default=float)
print(f"{len(rows)} SMC trades saved ({sum(1 for r in rows if r['sig'])} with signal features)")
