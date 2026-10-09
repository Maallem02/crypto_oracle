"""Real account went $52.45 (Wed ~21:45) -> $0.00 (Thu 18:2x). Every deal since
Wed evening, bot (magic 234000) vs manual (magic 0), plus balance operations.
"""
from datetime import datetime, timedelta

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
a = mt5.account_info()
print(f"account {a.login} {a.server} {'REAL' if a.trade_mode == 2 else 'DEMO'}  "
      f"balance {a.balance:.2f}  equity {a.equity:.2f}\n")
d = mt5.history_deals_get(datetime(2026, 9, 23, 19, 0), datetime.now() + timedelta(hours=6)) or []
KIND = {0: "BUY", 1: "SELL", 2: "BALANCE", 3: "CREDIT"}
IO = {0: "IN ", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
run = 52.45
bot = manual = funding = 0.0
for x in sorted(d, key=lambda z: z.time):
    pl = x.profit + x.commission + x.swap
    run += pl
    who = "BOT   " if x.magic == 234000 else ("manual" if x.type in (0, 1) else "      ")
    if x.type in (0, 1):
        if x.magic == 234000:
            bot += pl
        else:
            manual += pl
    else:
        funding += pl
    io = IO.get(x.entry, "?") if x.type in (0, 1) else "   "
    print(f"  {datetime.fromtimestamp(x.time):%a %H:%M:%S}  {who} {KIND.get(x.type, str(x.type)):7s} {io} "
          f"{x.symbol or '-':9s} vol={x.volume:5.2f} @ {x.price:11.3f}  P/L={pl:+8.2f}  "
          f"bal~{run:7.2f}  \"{x.comment}\"")
print(f"\n  bot trades      {bot:+8.2f}")
print(f"  manual trades   {manual:+8.2f}")
print(f"  deposits/withdrawals {funding:+8.2f}")
open_ = mt5.positions_get() or []
print(f"\n  open positions now: {len(open_)}")
for p in open_:
    print(f"    {p.symbol} {'BUY' if p.type == 0 else 'SELL'} vol={p.volume} PL={p.profit:+.2f} "
          f"{'(bot)' if p.magic == 234000 else '(manual)'}")
mt5.shutdown()
