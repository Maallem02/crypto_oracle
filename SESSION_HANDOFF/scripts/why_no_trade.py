"""Why no trade on the real account? Walk the whole funnel since the session
started, in order - the answer is wherever it stops.

  1. is the bot alive / in session hours at all?
  2. did the strategy find any setup?
  3. was a found setup rejected (cap, stop too wide, risk sizing)?
  4. was an ORDER placed but never filled (limit expired after 60 min)?
  5. did a fill happen?
"""
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import requests

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
BOT = 234000

now = datetime.now()
u = datetime.now(timezone.utc)
try:
    st = requests.get("http://127.0.0.1:8000/trading/scalping/status", timeout=10).json()
except Exception as e:
    st = None
    print(f"BACKEND UNREACHABLE: {e}")

print("=" * 92)
print("1. ALIVE AND IN SESSION?")
print("=" * 92)
print(f"  now: local {now:%a %H:%M}   UTC {u:%H:%M}")
dead = u.hour >= 21 or u.hour < 2
print(f"  dead zone 21:00-02:00 UTC: {'YES - no trading until 03:00 local' if dead else 'no'}")
if st:
    ls = st.get("last_scan")
    age = (now - datetime.fromisoformat(ls)).total_seconds() / 60 if ls else None
    print(f"  running={st.get('running')}  stopped_reason={st.get('stopped_reason')}  "
          f"last scan {f'{age:.1f} min ago' if age is not None else 'never'}")
    start = st.get("session_start") or now.strftime("%Y-%m-%dT00:00:00")
else:
    start = now.strftime("%Y-%m-%dT00:00:00")
print(f"  session started {start[:19]}  ({(now - datetime.fromisoformat(start[:19])).total_seconds()/60:.0f} min ago)")

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

print("\n" + "=" * 92)
print("2 & 3. WHAT THE STRATEGY FOUND, PER SYMBOL (since session start)")
print("=" * 92)
SETUP_FOUND = ("place_trade_failed", "sl_too_wide", "risk_entry_reject", "risk_reject",
               "pending_failed", "pending_only", "plafond", "max_positions",
               "cooldown", "consensus_gate", "htf_filter", "fast_trend", "macro4h",
               "dir_breaker", "news", "meta_")
for sym in ("BTC", "ETH", "XAGUSD"):
    rows = list(db.execute("select timestamp, reason from rejection_log "
                           "where timestamp >= ? and upper(symbol)=? order by timestamp",
                           (start, sym)))
    c = Counter()
    for r in rows:
        c[str(r["reason"]).split(":")[0][:48]] += 1
    print(f"\n  {sym}: {len(rows)} checks")
    for k, v in c.most_common(8):
        mark = "   <- a setup existed but was filtered" if any(s in k for s in SETUP_FOUND) else ""
        print(f"    {v:5d}  {k}{mark}")
    late = [r for r in rows if any(s in str(r["reason"]) for s in
                                   ("place_trade_failed", "risk_entry_reject", "plafond",
                                    "pending_failed", "sl_too_wide"))]
    if late:
        print(f"    last filtered setup: {late[-1]['timestamp'][11:19]}  "
              f"{str(late[-1]['reason'])[:110]}")

print("\n" + "=" * 92)
print("4 & 5. ORDERS THE BOT SENT TO THE BROKER (real account, since session start)")
print("=" * 92)
mt5.initialize(timeout=10000)
a = mt5.account_info()
sdt = datetime.fromisoformat(start[:19])
orders = [o for o in (mt5.history_orders_get(sdt - timedelta(minutes=1),
                                             now + timedelta(hours=6)) or [])
          if o.magic == BOT]
live = [o for o in (mt5.orders_get() or []) if o.magic == BOT]
deals = [d for d in (mt5.history_deals_get(sdt - timedelta(minutes=1),
                                           now + timedelta(hours=6)) or [])
         if d.magic == BOT and d.type in (0, 1)]
TYPES = {0: "MARKET BUY", 1: "MARKET SELL", 2: "BUY LIMIT", 3: "SELL LIMIT"}
STATES = {0: "started", 1: "placed", 2: "CANCELLED", 3: "partial", 4: "FILLED",
          5: "rejected", 6: "EXPIRED"}
print(f"  account {a.login} ({'REAL' if a.trade_mode == 2 else 'DEMO'})  equity ${a.equity:.2f}")
print(f"  bot orders in history: {len(orders)}   resting now: {len(live)}   bot fills: {len(deals)}")
for o in sorted(orders, key=lambda z: z.time_setup):
    life = (o.time_done - o.time_setup) / 60 if o.time_done else 0
    print(f"    {datetime.fromtimestamp(o.time_setup):%H:%M}  {o.symbol:9s} "
          f"{TYPES.get(o.type, o.type):11s} @ {o.price_open:<10} "
          f"{STATES.get(o.state, o.state):9s} after {life:5.1f} min")
for o in live:
    print(f"    RESTING  {o.symbol:9s} {TYPES.get(o.type, o.type)} @ {o.price_open} "
          f"(placed {datetime.fromtimestamp(o.time_setup):%H:%M})")
mt5.shutdown()
