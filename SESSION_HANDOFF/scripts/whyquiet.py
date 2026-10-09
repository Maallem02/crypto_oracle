"""Only 1-4 evaluations per symbol in 3 hours, where ~60 is normal (scan every
3 min). Symbols are being SKIPPED, and the skips that matter are silent:

    BLOCKED_SYMBOLS      -> silent by design
    is_trading_session   -> silent by design
    streak pause (1h)    -> silent by design  <- the 08-19/20 deadlock lived here

health_check flags XAUUSD's pause condition as TRUE. Find out whether the
pause is cycling correctly or stuck, and what today actually did.
"""
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

now = datetime.now()
u = datetime.now(timezone.utc)
print(f"local {now:%a %Y-%m-%d %H:%M}   UTC {u:%H:%M}  weekday={u.weekday()}")
print(f"dead zone active: {u.hour >= 21 or u.hour < 2}   weekend: {u.weekday() >= 5}\n")

print("=" * 92)
print("EVALUATIONS PER HOUR TODAY (a healthy hour is ~20 per symbol)")
print("=" * 92)
rows = list(db.execute(
    "select timestamp, symbol from rejection_log where timestamp >= ?",
    (now.strftime("%Y-%m-%dT00:00:00"),)))
byhour = defaultdict(Counter)
for r in rows:
    byhour[r["timestamp"][11:13]][(r["symbol"] or "?").upper()] += 1
syms = ["BTC", "ETH", "XAGUSD", "XAUUSD"]
print(f"  {'hour':6s} " + " ".join(f"{s:>8s}" for s in syms))
for h in sorted(byhour):
    c = byhour[h]
    print(f"  {h}:00  " + " ".join(f"{c.get(s,0):8d}" for s in syms))

print("\n" + "=" * 92)
print("DISTINCT SCAN TIMESTAMPS TODAY - is the loop itself running?")
print("=" * 92)
ts = [r[0] for r in db.execute(
    "select distinct timestamp from rejection_log where timestamp >= ? order by timestamp",
    (now.strftime("%Y-%m-%dT00:00:00"),))]
print(f"  {len(ts)} scans logged today")
if ts:
    print(f"  first {ts[0][11:19]}   last {ts[-1][11:19]}")
    T = [datetime.fromisoformat(x) for x in ts]
    gaps = [(T[i-1], T[i], (T[i]-T[i-1]).total_seconds()/60)
            for i in range(1, len(T)) if (T[i]-T[i-1]).total_seconds() > 1500]
    for a, b, m in gaps:
        print(f"    GAP {a:%H:%M} -> {b:%H:%M}  ({m:.0f} min)")
    if not gaps:
        print("    no gaps over 25 min")

print("\n" + "=" * 92)
print("LIVE PAUSE STATE (from the running process)")
print("=" * 92)
try:
    st = requests.get("http://127.0.0.1:8000/trading/scalping/status",
                      timeout=8).json()
    print(f"  running={st.get('running')}  trades_today={st.get('trades_today')}  "
          f"daily_pnl={st.get('daily_pnl')}")
    for k in ("paused_symbols", "symbol_paused_until", "stopped_reason"):
        if k in st:
            print(f"  {k} = {st[k]}")
except Exception as e:
    print(f"  status error: {e}")

try:
    sl = requests.get("http://127.0.0.1:8000/trading/scalping/scan-log?limit=25",
                      timeout=8).json()
    items = sl if isinstance(sl, list) else sl.get("rejections", sl)
    print(f"\n  last scan-log entries:")
    for it in (items or [])[-12:]:
        if isinstance(it, dict):
            print(f"    {str(it.get('ts',''))[11:19]}  {str(it.get('symbol','')):8s} "
                  f"{str(it.get('reason',''))[:70]}")
except Exception as e:
    print(f"  scan-log error: {e}")

print("\n" + "=" * 92)
print("TODAY'S TRADES")
print("=" * 92)
import MetaTrader5 as mt5
mt5.initialize(timeout=10000)
d = mt5.history_deals_get(datetime(now.year, now.month, now.day),
                          datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for x in d:
    if x.position_id and x.type in (0, 1):
        pos[x.position_id].append(x)
rows2 = []
for pid, ds in pos.items():
    ds.sort(key=lambda z: z.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    rows2.append((datetime.fromtimestamp(ins[0].time), ins[0].symbol,
                  "BUY" if ins[0].type == 0 else "SELL",
                  sum(x.profit + x.commission + x.swap for x in ds),
                  (outs[-1].time - ins[0].time) / 60.0))
rows2.sort()
run = 0.0
for t, s, dr, p, h in rows2:
    run += p
    print(f"  {t:%H:%M}  {s:9s} {dr:4s} {p:+8.2f}  held {h:5.0f}m   run {run:+8.2f}")
acc = mt5.account_info()
print(f"\n  today net: {run:+.2f}    balance ${acc.balance:.2f}")
mt5.shutdown()
