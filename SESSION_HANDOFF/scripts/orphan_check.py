"""The backend died 15h ago. Did it leave anything open and unmanaged?

An orphaned position has no trail, no break-even ratchet and no news purge for
however long the bot is down. A resting pending order can also fill into a
market the bot is no longer watching.
"""
from datetime import datetime, timedelta

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
a = mt5.account_info()
print(f"balance ${a.balance:.2f}   equity ${a.equity:.2f}   "
      f"floating ${a.equity - a.balance:+.2f}   margin ${a.margin:.2f}")

pos = mt5.positions_get() or []
print(f"\nOPEN POSITIONS: {len(pos)}")
for p in pos:
    age = (datetime.now() - datetime.fromtimestamp(p.time)).total_seconds() / 3600
    print(f"  {p.symbol:9s} {'BUY' if p.type==0 else 'SELL':4s} vol={p.volume} "
          f"open={p.price_open} now={p.price_current} SL={p.sl} TP={p.tp} "
          f"P/L=${p.profit:+.2f}  age={age:.1f}h  magic={p.magic}")
    if p.sl == 0:
        print("      *** NO STOP LOSS ***")

orders = mt5.orders_get() or []
print(f"\nPENDING ORDERS: {len(orders)}")
for o in orders:
    age = (datetime.now() - datetime.fromtimestamp(o.time_setup)).total_seconds() / 60
    print(f"  {o.symbol:9s} type={o.type} vol={o.volume_current} @ {o.price_open} "
          f"SL={o.sl} TP={o.tp}  age={age:.0f} min  magic={o.magic}")

print("\nDEALS SINCE MONDAY:")
d = mt5.history_deals_get(datetime(2026, 9, 14), datetime.now() + timedelta(hours=6)) or []
net = 0.0
for x in sorted(d, key=lambda z: z.time):
    if x.type not in (0, 1):
        continue
    net += x.profit + x.commission + x.swap
    print(f"  {datetime.fromtimestamp(x.time):%m-%d %H:%M}  {x.symbol:9s} "
          f"{'in ' if x.entry==0 else 'out'} {x.volume:5.2f} @ {x.price:10.3f}  "
          f"P/L={x.profit+x.commission+x.swap:+8.2f}")
print(f"\n  net since Monday: ${net:+.2f}")
mt5.shutdown()
