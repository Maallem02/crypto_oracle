"""Balance says 141.64 but peak says 159.18. Reconcile it.

Peak above the 150 deposit means either a second deposit, or the peak is being
read off a balance curve that includes deposits. Walk every deal - trades AND
balance operations - in order.
"""
from datetime import datetime, timedelta

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
a = mt5.account_info()
print(f"NOW: balance ${a.balance:.2f}  equity ${a.equity:.2f}  "
      f"floating ${a.equity-a.balance:+.2f}")

d = mt5.history_deals_get(datetime(2026, 9, 12), datetime.now() + timedelta(hours=6)) or []
d = sorted(d, key=lambda z: z.time)

print(f"\n{'time':17s} {'type':9s} {'symbol':9s} {'in/out':6s} "
      f"{'volume':>7s} {'price':>11s} {'P/L':>9s} {'running':>9s}")
# reconstruct the balance curve backwards from the current balance
tot = sum(x.profit + x.commission + x.swap for x in d)
run = a.balance - tot
start = run
peak = run
for x in d:
    amt = x.profit + x.commission + x.swap
    run += amt
    peak = max(peak, run)
    kind = {0: "buy", 1: "sell", 2: "balance", 3: "credit"}.get(x.type, str(x.type))
    io = "" if x.type not in (0, 1) else ("in" if x.entry == 0 else "out")
    print(f"{datetime.fromtimestamp(x.time):%m-%d %H:%M:%S} {kind:9s} "
          f"{x.symbol or '-':9s} {io:6s} {x.volume:7.2f} {x.price:11.3f} "
          f"{amt:+9.2f} {run:9.2f}")

print(f"\n  start of window  ${start:.2f}")
print(f"  running peak     ${peak:.2f}")
print(f"  now              ${run:.2f}   (drawdown ${run-peak:+.2f} = "
      f"{100*(run-peak)/peak:.1f}%)")

dep = [x for x in d if x.type == 2]
if dep:
    print("\n  BALANCE OPERATIONS (deposits/withdrawals):")
    for x in dep:
        print(f"    {datetime.fromtimestamp(x.time):%m-%d %H:%M}  "
              f"${x.profit:+.2f}  {x.comment}")
else:
    print("\n  no deposits/withdrawals in this window")
mt5.shutdown()
