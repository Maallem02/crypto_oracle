"""Today's last 5 losing trades: entry context, path, news, market move. Read-only."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import MetaTrader5 as mt5

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from features.news.calendar import COUNTRY_CURRENCY, _fetch, _get_currencies, _parse_dt
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
BOT = {234000: "SMC", 234100: "VP"}
db = sqlite3.connect(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db")
db.row_factory = sqlite3.Row
mt5.initialize(timeout=10000)
day0 = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
lt = lambda ts: datetime.fromtimestamp(ts, UTC).astimezone()
events = []
for ev in _fetch():
    if ev.get("impact") in ("High", "Medium") and _parse_dt(ev):
        cur = COUNTRY_CURRENCY.get(ev.get("country", ""), (ev.get("country") or "")[:3].upper())
        events.append((_parse_dt(ev), cur, ev.get("impact"), ev.get("title")))

pos = {}
for d in mt5.history_deals_get(day0.astimezone(UTC) - timedelta(days=3), datetime.now(UTC) + timedelta(hours=3)) or []:
    if d.type in (0, 1):
        pos.setdefault(d.position_id, []).append(d)
losers = []
for pid, ds in pos.items():
    full = sorted(mt5.history_deals_get(position=pid) or ds, key=lambda d: d.time)
    outs = [d for d in full if d.entry == 1]
    ins = [d for d in full if d.entry == 0]
    if not outs or not ins or outs[-1].time < day0.timestamp() or ins[0].magic not in BOT:
        continue
    pl = sum(d.profit + d.commission + d.swap for d in full)
    if pl < 0:
        losers.append((outs[-1].time, pid, ins[0], outs[-1], pl))
losers.sort()
last5 = losers[-5:]
print(f"last 5 losers: total {sum(x[4] for x in last5):+.2f}\n")
for t_out, pid, i, o, pl in last5:
    buy = i.type == 0
    sym = i.symbol
    bot = BOT[i.magic]
    vp = db.execute("SELECT setup, sl, tp, reason, poc, vah, val, data FROM vp_log WHERE ticket=? AND status='taken'", (pid,)).fetchone()
    ts = db.execute("SELECT sl, tp1, scalping_score, htf_consensus, adx, atr, rr_ratio, structure FROM trade_signals WHERE ticket=? ORDER BY id DESC", (pid,)).fetchone()
    sl0 = (vp["sl"] if vp else (ts["sl"] if ts else None))
    tp0 = (vp["tp"] if vp else (ts["tp1"] if ts else None))
    rp = abs(i.price - sl0) if sl0 else None
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, datetime.fromtimestamp(i.time, UTC) - timedelta(days=3),
                               datetime.fromtimestamp(i.time, UTC))
    atr = float(atr_series(m15)[-1]) if m15 is not None and len(m15) > 20 else None
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, datetime.fromtimestamp(i.time - i.time % 60, UTC),
                              datetime.fromtimestamp(o.time + 60, UTC))
    fav = ((m1["high"] - i.price) if buy else (i.price - m1["low"])) / rp
    k = int(np.argmax(fav))
    d1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, day0.astimezone(UTC) - timedelta(days=1), datetime.now(UTC))
    day = d1[-1]
    day_pct = 100 * (day["close"] - day["open"]) / day["open"]
    move_at_entry = (i.price - day["open"]) / atr if atr else 0
    curs = _get_currencies(sym.rstrip("m"))
    near = [e for e in events if e[1] in curs and i.time - 1800 <= e[0].timestamp() <= o.time + 300]
    # the 5 minutes around the exit: how violent was the move?
    m1x = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, datetime.fromtimestamp(o.time - 300, UTC),
                               datetime.fromtimestamp(o.time + 60, UTC))
    spike = (float(m1x["high"].max() - m1x["low"].min()) / atr) if atr and m1x is not None and len(m1x) else 0
    risk_usd = -mt5.order_calc_profit(i.type, sym, i.volume, i.price, sl0) if sl0 else None
    bal = mt5.account_info().balance
    print(f"=== {lt(i.time):%H:%M} -> {lt(o.time):%H:%M}  {bot} {sym} {'BUY' if buy else 'SELL'} {i.volume}   {pl:+.2f}")
    if vp:
        d = json.loads(vp["data"] or "{}")
        room = ((vp["vah"] - i.price) if buy else (i.price - vp["val"])) / rp
        print(f"   why opened: VP {vp['setup']} — {vp['reason']}; levels VAL {vp['val']:.5g} POC {vp['poc']:.5g} VAH {vp['vah']:.5g}; "
              f"room to far edge {room:.2f}R")
    elif ts:
        print(f"   why opened: SMC score {ts['scalping_score']}, HTF {ts['htf_consensus']}, ADX {ts['adx']}, structure {ts['structure']}, RR {ts['rr_ratio']}")
    print(f"   entry {i.price:.5g}  stop {sl0:.5g}  target {tp0:.5g}  -> stop = {rp / atr:.2f}x ATR(15m), risk ${risk_usd:.2f} "
          f"({100 * risk_usd / bal:.0f}% of balance)")
    print(f"   path: best point {fav.max():+.2f}R after {(int(m1['time'][k]) - i.time) / 60:.0f} min; held {(o.time - i.time) / 60:.0f} min")
    print(f"   market: {sym} today {day_pct:+.2f}% ({'UP' if day_pct > 0 else 'DOWN'}); at entry price was {move_at_entry:+.1f} ATR from the day's open "
          f"-> trade {'WITH' if (buy == (move_at_entry > 0)) else 'AGAINST'} the day's move")
    print(f"   last 5 min before the stop: range {spike:.1f}x ATR" + ("  <- violent spike" if spike > 2 else ""))
    if near:
        print("   news: " + "; ".join(f"{e[0].astimezone():%H:%M} {e[1]} {e[2]} {e[3]}" for e in near))
    print()
mt5.shutdown()
