"""Closed trades per bot this week on the current (demo) account, from MT5 deals."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

UTC = timezone.utc
mt5.initialize(timeout=10000)
a = mt5.account_info()
now = datetime.now(UTC)
since = datetime(2026, 9, 21, tzinfo=UTC)                  # Monday
deals = mt5.history_deals_get(since, now + timedelta(hours=3)) or []
print(f"account {a.login} {'DEMO' if a.trade_mode == 0 else 'REAL'}  balance {a.balance:.2f}  "
      f"equity {a.equity:.2f}  (deals since Mon 09-21)")
cash = [d for d in deals if d.type == 2]
for d in cash:
    print(f"  balance op {datetime.fromtimestamp(d.time, UTC):%m-%d %H:%M} {d.profit:+.2f} '{d.comment}'")

pos = defaultdict(list)
for d in deals:
    if d.type in (0, 1):
        pos[d.position_id].append(d)
rows = []
for pid, ds in pos.items():
    ins = [d for d in ds if d.entry == 0]
    outs = [d for d in ds if d.entry == 1]
    if not ins or not outs:
        continue
    i = ins[0]
    pnl = sum(d.profit + d.commission + d.swap for d in ds)
    last = max(outs, key=lambda d: d.time)
    c = (last.comment or "").lower()
    ex = "TP" if "[tp" in c else "SL" if "[sl" in c else "other"
    bot = {234000: "SMC", 234100: "VP"}.get(i.magic, "manual")
    rows.append((datetime.fromtimestamp(i.time, UTC), bot, i.symbol.replace("m", "", 1) if False else i.symbol,
                 "buy" if i.type == 0 else "sell", i.volume, pnl, ex,
                 (last.time - i.time) / 60))
rows.sort()
for bot in ("SMC", "VP", "manual"):
    r = [x for x in rows if x[1] == bot]
    if not r:
        print(f"\n{bot}: no closed trades this week")
        continue
    w = [x for x in r if x[5] > 0]
    gp, gl = sum(x[5] for x in w), -sum(x[5] for x in r if x[5] <= 0)
    print(f"\n{bot}: {len(r)} closed  wins {len(w)} ({100 * len(w) / len(r):.0f}%)  "
          f"net {sum(x[5] for x in r):+.2f}  PF {gp / gl if gl else float('inf'):.2f}")
    by = defaultdict(list)
    for x in r:
        by[x[2]].append(x[5])
    for s, v in sorted(by.items()):
        print(f"    {s:9s} {len(v):3d} trades  net {sum(v):+7.2f}  wins {sum(1 for p in v if p > 0)}")
    for x in r[-8:]:
        print(f"      {x[0]:%a %d %H:%M} {x[2]:9s} {x[3]:4s} {x[4]:5.2f}  {x[5]:+7.2f}  {x[6]:5s} "
              f"{x[7]:5.0f} min")
mt5.shutdown()
